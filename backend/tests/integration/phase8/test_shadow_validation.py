"""Phase 8 Step 6: stratified shadow validation + circuit breaker, and the
shadow gate on Phase 6 activation (through the Phase 7 guard hook)."""
import json

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.pipeline.hashing import sha256_hex
from app.schema.adapter import AdapterMapping
from app.services.phase8 import shadow_service as svc
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, _phase7_defaults, ingest, isolated_stores, users  # noqa: F401
from tests.integration.test_learning_flow import ADAPTER, base_log, with_added

TABLES = ("events", "source_baselines", "source_baseline_history", "onboarded_adapters", "learning_sessions",
          "event_revisions", "event_lineage_compact", "event_raw_storage", "evidence_batch_members")


@pytest.fixture(autouse=True)
def _drift_settings(monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85), ("onboarding_min_match_rate", 0.9),
                        ("onboarding_reject_below_match_rate", 0.5), ("onboarding_min_mapping_coverage", 0.3),
                        ("phase8_shadow_gate", "if_present")):
        monkeypatch.setattr(s, name, value)


@pytest.fixture()
def session(client):
    """Onboarded ACMEFW v1, production logs, an accepted drift and a validated learning proposal."""
    s = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i) for i in range(12)]}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    for i in range(6):
        ingest(client, base_log(100 + i))
    drifted = ingest(client, with_added(1))
    assert client.post(f"{API}/events/{drifted['event_id']}/drift/accept",
                       json={"mode": "add_variant", "note": "legit"}).status_code == 200
    ls = client.post(f"{API}/events/{drifted['event_id']}/learning/propose", json={"assistant": "offline"}).json()
    assert ls["status"] == "VALIDATED" and ls["candidate"]
    return ls


def shadow(client, ls, headers=None):
    r = client.post(f"{API}/shadow/runs", json={"learning_session_id": ls["id"]}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, ls, headers=None, **extra):
    return client.post(f"{API}/learning/sessions/{ls['id']}/approve",
                       json={"proposal_version": ls["proposal_version"], "note": "reviewed", **extra}, headers=headers)


def activate(client, ls, headers=None, **body):
    return client.post(f"{API}/learning/sessions/{ls['id']}/activate", json=body, headers=headers)


def fingerprint():
    with TestSessionLocal() as db:
        return {t: db.execute(text(f"SELECT count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) "
                                   f"FROM {t} x")).one() for t in TABLES}


def old_and_candidate(ls):
    with TestSessionLocal() as db:
        row = db.execute(text("SELECT mapping FROM onboarded_adapters WHERE adapter_id=:a AND status='ACTIVE'"),
                         {"a": ADAPTER}).scalar()
    return row, AdapterMapping.model_validate({**ls["candidate"], "source": "onboarded"})


# --- strata, coverage, no mutation ----------------------------------------------------------------------------


def test_insufficient_strata_are_reported_never_padded(client, session):
    run = shadow(client, session)
    assert run["verdict"] == "REVIEW_REQUIRED" and run["breaker_tripped"] is False
    coverage = next(r for r in run["reasons"] if r["code"] == "INSUFFICIENT_COVERAGE")
    for name in svc.STRATA:
        s = run["strata"][name]
        assert s["target"] == 25 and len(s["selected"]) == min(25, s["available"])
        assert s["sufficient"] is (len(s["selected"]) >= 25)
        assert coverage["strata"][name] == {"selected": len(s["selected"]), "target": 25}
    # 6 production logs; the accepted drift keeps drift.status=DRIFT (with its review) -> DRIFT stratum.
    assert run["strata"]["NORMAL"]["available"] == 6 and run["strata"]["DRIFT"]["available"] == 1
    assert "source median" in run["strata"]["EXTENSION_HEAVY"]["rule"]
    assert run["sample_count"] == sum(len(s["selected"]) for s in run["strata"].values())
    assert run["summary"]["started_at"] and run["summary"]["finished_at"] and run["latency"]["new"]["p95"] is not None


def test_strata_are_disjoint_and_deterministic(client, session):
    first, second = shadow(client, session), shadow(client, session)
    assert {n: s["selected"] for n, s in first["strata"].items()} == {n: s["selected"] for n, s in second["strata"].items()}
    ids = [i for s in first["strata"].values() for i in s["selected"]]
    assert len(ids) == len(set(ids))


def test_shadow_run_never_mutates_production(client, session):
    before = fingerprint()
    run = shadow(client, session)
    assert fingerprint() == before
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT verdict FROM shadow_runs WHERE id=:i"), {"i": run["id"]}).scalar() == run["verdict"]


# --- full coverage + clean candidate --------------------------------------------------------------------------


def _insert(rows):
    with TestSessionLocal() as db:
        db.add_all(rows)
        db.commit()


def _event(raw, **kw):
    base = dict(event_id=generate_event_id(), raw_event=raw, raw_hash=sha256_hex(raw), format_detected="kv",
                status="SUCCESS", extensions={}, processing_metadata={}, warnings=[])
    base.update(kw)
    with TestSessionLocal() as db:  # received_at: now
        base.setdefault("received_at", db.execute(text("SELECT now()")).scalar())
    return Event(**base)


def test_clean_candidate_with_full_coverage_passes(client, session):
    rows = []
    for i in range(25):
        rows.append(_event(f"garbage line {i}", status="FAILED", format_detected="unknown"))
        rows.append(_event(with_added(300 + i), adapter_id=ADAPTER, adapter_version="1", status="UNDER_REVIEW",
                           processing_metadata={"drift": {"status": "DRIFT", "source_key": ADAPTER}}))
        rows.append(_event(base_log(400 + i), adapter_id=ADAPTER, adapter_version="1", status="PARTIAL"))
        rows.append(_event(base_log(500 + i), adapter_id=ADAPTER, adapter_version="1",
                           extensions={f"k{j}": j for j in range(30)}))
        rows.append(_event(base_log(600 + i), adapter_id=ADAPTER, adapter_version="1"))
        raw = json.dumps({"user": f"u{i}", "action": "login"})
        rows.append(_event(raw, adapter_id="json_generic", format_detected="json"))
    _insert(rows)
    run = shadow(client, session)
    assert all(s["sufficient"] for s in run["strata"].values()) and run["sample_count"] == 150
    assert run["verdict"] == "PASSED", run["reasons"]
    totals = run["summary"]["totals"]
    assert totals["raw_hash_mismatches"] == totals["evidence_loss"] == totals["review_differences"] == 0
    assert totals["improvements"] >= 1  # the learned fields become mapped: recorded, never blocking
    assert run["strata"]["FAILED"]["results"]["old_parse_success"] == 0
    # A PASSED run for the current proposal lets activation through; the guard verdict is audited.
    assert approve(client, session).status_code == 200
    assert activate(client, session).status_code == 200
    audit = client.get(f"{API}/governance/audit", params={"action": "LEARNING_ACTIVATE"}).json()["items"][0]
    gate = next(g for g in audit["details"]["guards"] if g["guard"] == "shadow_gate")
    assert gate["verdict"] == "ALLOW" and gate["evidence"]["shadow_run_id"] == run["id"]


# --- circuit breaker ----------------------------------------------------------------------------------------------


def test_status_degradation_is_blocked(client, session):
    mapping, _ = old_and_candidate(session)
    broken = AdapterMapping.model_validate({**mapping, "version": "2", "source": "onboarded",
                                            "field_map": {**mapping["field_map"],
                                                          "msg": {"target": "network.dst_port", "type": "int"}}})
    with TestSessionLocal() as db:
        result = svc.compare(db, ADAPTER, broken)
    verdict, tripped, reasons = svc.verdict(result)
    assert verdict == "BLOCKED" and tripped
    degraded = next(r for r in reasons if r["code"] == "STATUS_DEGRADED")
    assert degraded["critical"] and degraded["event_ids"]
    change = next(c for d in result["diffs"] for c in d["changes"] if c["kind"] == "STATUS_DEGRADED")
    assert change == {"kind": "STATUS_DEGRADED", "old": "SUCCESS", "new": "PARTIAL"}


def _patch_new_side(monkeypatch, mutate):
    real = svc._process

    def fake(raw, received_at, registry):
        fields = real(raw, received_at, registry)
        return mutate(fields) if getattr(registry, "_is_candidate", False) else fields

    real_regs = svc.registries

    def tagged(db, source, candidate):
        old, new = real_regs(db, source, candidate)
        new._is_candidate = True
        return old, new

    monkeypatch.setattr(svc, "_process", fake)
    monkeypatch.setattr(svc, "registries", tagged)


def test_evidence_loss_is_blocked_and_gates_activation(client, session, monkeypatch):
    def drop_extensions(fields):
        return {**fields, "extensions": {}}  # a buggy candidate silently dropping preserved fields

    _patch_new_side(monkeypatch, drop_extensions)
    run = shadow(client, session)
    assert run["verdict"] == "BLOCKED" and run["breaker_tripped"]
    loss = next(r for r in run["reasons"] if r["code"] == "EVIDENCE_LOSS")
    assert loss["count"] >= 1 and run["summary"]["circuit_breaker_reason"] == ["EVIDENCE_LOSS"]
    monkeypatch.undo()
    assert approve(client, session).status_code == 200
    r = activate(client, session)
    assert r.status_code == 409 and "BLOCKED the candidate (EVIDENCE_LOSS)" in r.json()["error"]["message"]
    assert client.get(f"{API}/learning/sessions/{session['id']}").json()["status"] == "APPROVED"  # not activated
    audit = client.get(f"{API}/governance/audit", params={"action": "LEARNING_ACTIVATE"}).json()["items"][0]
    assert audit["decision"] == "DENIED"
    shadow_audit = client.get(f"{API}/governance/audit", params={"action": "SHADOW_RUN"}).json()["items"]
    assert shadow_audit[-1]["details"]["verdict"] == "BLOCKED"


def test_raw_hash_mismatch_is_blocked(client, session, monkeypatch):
    _patch_new_side(monkeypatch, lambda f: {**f, "raw_event": f["raw_event"] + " "})
    run = shadow(client, session)
    assert run["verdict"] == "BLOCKED" and run["breaker_tripped"]
    mismatch = next(r for r in run["reasons"] if r["code"] == "RAW_HASH_MISMATCH")
    assert mismatch["critical"] and mismatch["count"] == run["sample_count"]  # every sampled event altered
    assert run["summary"]["totals"]["raw_hash_mismatches"] == run["sample_count"]


def test_p95_latency_over_3x_is_blocked(client, session, monkeypatch):
    real = svc._timed

    def slow(raw, received_at, registry):
        fields, ms = real(raw, received_at, registry)
        return fields, ms + (50.0 if registry is slow.new else 0.0)

    real_regs = svc.registries

    def capture(db, source, candidate):
        old, new = real_regs(db, source, candidate)
        slow.new = new
        return old, new

    monkeypatch.setattr(svc, "_timed", slow)
    monkeypatch.setattr(svc, "registries", capture)
    run = shadow(client, session)
    assert run["verdict"] == "BLOCKED"
    lat = next(r for r in run["reasons"] if r["code"] == "LATENCY_P95")
    assert lat["new_p95_ms"] > 3 * lat["old_p95_ms"]


def test_shadow_error_fails_closed(client, session, monkeypatch):
    monkeypatch.setattr(svc, "compare", lambda *a, **k: 1 / 0)
    run = shadow(client, session)
    assert run["verdict"] == "BLOCKED" and run["reasons"][0]["code"] == "SHADOW_ERROR"


# --- gate modes and binding to the current proposal -----------------------------------------------------------------


def test_review_required_run_needs_elevated_activation(client, session, users):
    shadow(client, session)  # insufficient coverage -> REVIEW_REQUIRED
    assert approve(client, session, users["eng1"]).status_code == 200
    r = activate(client, session, users["eng2"])
    assert r.status_code == 403 and "requires review (INSUFFICIENT_COVERAGE" in r.json()["error"]["message"]
    r = activate(client, session, users["admin"], note="coverage is thin but vendor change is confirmed")
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"


def test_no_shadow_run_keeps_phase6_behavior_in_if_present_mode(client, session):
    assert approve(client, session).status_code == 200
    assert activate(client, session).status_code == 200
    audit = client.get(f"{API}/governance/audit", params={"action": "LEARNING_ACTIVATE"}).json()["items"][0]
    assert not any(g["guard"] == "shadow_gate" for g in audit["details"].get("guards", []))


def test_required_mode_blocks_activation_without_a_run(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "phase8_shadow_gate", "required")
    assert approve(client, session).status_code == 200
    r = activate(client, session)
    assert r.status_code == 409 and "No shadow validation" in r.json()["error"]["message"]
    r = approve(client, session, activate=True)  # the one-call route is gated too
    assert r.status_code in (409,)


def test_run_for_an_older_proposal_does_not_gate(client, session, monkeypatch):
    _patch_new_side(monkeypatch, lambda f: {**f, "extensions": {}})
    assert shadow(client, session)["verdict"] == "BLOCKED"
    monkeypatch.undo()
    with TestSessionLocal() as db:  # a newer proposal (as a human re-submission would create)
        ls = db.get(LearningSession, session["id"])
        ls.proposal_version += 1
        db.commit()
    with TestSessionLocal() as db:
        assert svc.latest_for_session(db, db.get(LearningSession, session["id"])) is None


def test_shadow_endpoints(client, session):
    run = shadow(client, session)
    assert client.get(f"{API}/shadow/runs/{run['id']}").json()["id"] == run["id"]
    assert [r["id"] for r in client.get(f"{API}/shadow/runs", params={"learning_session_id": session["id"]}).json()["items"]] == [run["id"]]
    assert client.get(f"{API}/shadow/runs/NOPE").status_code == 404
    assert client.post(f"{API}/shadow/runs", json={"learning_session_id": "NOPE"}).status_code == 404
