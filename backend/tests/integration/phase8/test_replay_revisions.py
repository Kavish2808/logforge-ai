"""Phase 8 Step 7: append-only revisions, rate-limited replay through the
existing reprocess path, the >10,000-event governance gate and
revision-aware rollback."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.phase8 import lineage_codec as codec
from app.services import evidence_service
from app.services.phase8 import replay_service as svc
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, _phase7_defaults, ingest, isolated_stores, users  # noqa: F401
from tests.integration.test_learning_flow import ADAPTER, base_log, with_added

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _drift_settings(monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85), ("onboarding_min_match_rate", 0.9),
                        ("onboarding_reject_below_match_rate", 0.5), ("onboarding_min_mapping_coverage", 0.3)):
        monkeypatch.setattr(s, name, value)


@pytest.fixture()
def events(client):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i) for i in range(12)]}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    out = [ingest(client, base_log(100 + i)) for i in range(7)]
    with TestSessionLocal() as db:
        assert evidence_service.seal(db, force=True)  # sealed Merkle evidence exists before any replay
    return out


def create(client, headers=None, expect=201, **body):
    r = client.post(f"{API}/replay/jobs", json={"adapter_id": ADAPTER, "reason": "re-run after mapping fix", **body},
                    headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def act(client, job, verb, headers=None, expect=200):
    r = client.post(f"{API}/replay/jobs/{job['id']}/{verb}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def job(client, job_id):
    return client.get(f"{API}/replay/jobs/{job_id}").json()


def run_slice(job_id, now):
    with TestSessionLocal() as db:
        return svc.run_slice(db, job_id, now=now)


def drive(job_id):
    with TestSessionLocal() as db:
        return svc.job_dict(svc.drive(db, job_id, sleep=lambda s: None))


def revisions(client, event_id):
    r = client.get(f"{API}/revisions/{event_id}")
    assert r.status_code == 200
    return r.json()


def replay_revisions(job_id):
    with TestSessionLocal() as db:
        return db.execute(text("SELECT event_id, count(*) FROM event_revisions WHERE replay_job_id=:j GROUP BY 1"),
                          {"j": job_id}).all()


# --- single / multi event ----------------------------------------------------------------------------------------


def test_single_event_replay_records_a_revision_and_keeps_evidence(client, events):
    e = events[3]
    at = datetime.fromisoformat(e["received_at"])
    j = create(client, window_start=at.isoformat(), window_end=(at + timedelta(microseconds=1)).isoformat())
    assert j["total"] == 1 and j["status"] == "PENDING" and j["target_version"] == "1"
    act(client, j, "start")
    done = drive(j["id"])
    assert (done["status"], done["processed"], done["succeeded"], done["failed"]) == ("COMPLETED", 1, 1, 0)
    assert done["integrity"]["raw_hash_mismatches"] == 0 and done["integrity"]["merkle"]["valid"] is True
    h = revisions(client, e["event_id"])
    assert [(r["revision_no"], r["trigger"], r["is_current"]) for r in h["revisions"]] == [
        (1, "ORIGINAL", False), (2, "REPLAY", True)]
    assert h["revisions"][1]["replay_job_id"] == j["id"] and h["revisions"][1]["resulting_status"] == "SUCCESS"
    assert h["revisions"][1]["parent_revision_id"] == h["revisions"][0]["id"]
    assert all(h["integrity"].values()) and all(r["raw_hash"] == e["raw_hash"] for r in h["revisions"])
    with TestSessionLocal() as db:
        v = evidence_service.verify_event(db, e["event_id"])
        assert v["status"] == "VERIFIED" and v["hash"]["valid"]
        row = db.execute(text("SELECT stage_mask, exception_mask FROM event_lineage_compact WHERE event_id=:i"),
                         {"i": e["event_id"]}).one()
    assert "REPLAY" in codec.decode(row.stage_mask, row.exception_mask).exceptions


def test_multi_event_replay_is_rate_limited_per_slice(client, events):
    j = create(client, rate_per_sec=3, batch_size=3)
    assert j["total"] == 7
    act(client, j, "start")
    assert run_slice(j["id"], T0)["processed"] == 3  # first slice: min(batch 3, ceil(rate 3))
    assert run_slice(j["id"], T0 + timedelta(seconds=0.4))["processed"] == 1  # 0.4 s x 3/s -> 1 token
    assert run_slice(j["id"], T0 + timedelta(seconds=0.5))["processed"] == 0  # bucket empty: backpressure
    assert run_slice(j["id"], T0 + timedelta(seconds=30))["processed"] == 3  # capped at batch_size
    final = run_slice(j["id"], T0 + timedelta(seconds=60))
    assert final["status"] == "COMPLETED" and final["processed"] == 0
    done = job(client, j["id"])
    assert (done["processed"], done["succeeded"], done["slices"]) == (7, 7, 5)
    assert len(replay_revisions(j["id"])) == 7 and all(n == 1 for _, n in replay_revisions(j["id"]))


def test_rate_per_minute_and_bounds(client, events):
    assert create(client, rate_per_minute=120)["rate_per_sec"] == 2.0
    create(client, expect=422, rate_per_sec=0)
    create(client, expect=422, batch_size=5000)
    r = client.post(f"{API}/replay/jobs", json={"adapter_id": ADAPTER, "reason": "  "})
    assert r.status_code == 422 and "reason" in r.json()["error"]["message"]
    assert client.post(f"{API}/replay/jobs", json={"adapter_id": "no_such", "reason": "x"}).status_code == 409


# --- pause / resume / cancel / idempotency ------------------------------------------------------------------------


def test_pause_and_resume(client, events):
    j = create(client, batch_size=3)
    act(client, j, "start")
    assert run_slice(j["id"], T0)["processed"] == 3
    act(client, j, "pause")
    assert run_slice(j["id"], T0 + timedelta(seconds=60))["processed"] == 0
    act(client, j, "pause", expect=409)
    act(client, j, "resume")
    done = drive(j["id"])
    assert done["status"] == "COMPLETED" and done["processed"] == 7 and done["skipped"] == 0
    assert sorted(n for _, n in replay_revisions(j["id"])) == [1] * 7


def test_cancellation_stops_between_events(client, events):
    j = create(client, batch_size=2)
    act(client, j, "start")
    run_slice(j["id"], T0)
    cancelled = act(client, j, "cancel")
    assert cancelled["status"] == "CANCELLED" and cancelled["completed_at"]
    assert run_slice(j["id"], T0 + timedelta(seconds=60))["processed"] == 0
    assert job(client, j["id"])["processed"] == 2 and len(replay_revisions(j["id"])) == 2
    act(client, j, "cancel", expect=409)
    act(client, j, "start", expect=409)


def test_lost_checkpoint_resumes_without_duplicate_revisions(client, events):
    j = create(client, batch_size=3)
    act(client, j, "start")
    run_slice(j["id"], T0)
    with TestSessionLocal() as db:  # simulate an interruption that lost the durable cursor
        db.execute(text("UPDATE replay_jobs SET cursor = jsonb_set(cursor, '{after}', 'null') WHERE id=:j"), {"j": j["id"]})
        db.commit()
    done = drive(j["id"])
    assert (done["status"], done["processed"], done["succeeded"], done["skipped"]) == ("COMPLETED", 10, 7, 3)
    assert sorted(n for _, n in replay_revisions(j["id"])) == [1] * 7


def test_failed_event_is_recorded_and_job_continues(client, events, monkeypatch):
    from app.services import ingestion_service

    real = ingestion_service.reprocess_event
    bad = events[2]["event_id"]

    def flaky(db, event):
        if event.event_id == bad:
            raise RuntimeError("pipeline bug")
        return real(db, event)

    monkeypatch.setattr(ingestion_service, "reprocess_event", flaky)
    j = create(client)
    act(client, j, "start")
    done = drive(j["id"])
    assert (done["status"], done["succeeded"], done["failed"]) == ("COMPLETED", 6, 1)
    assert done["errors"] == [{"event_id": bad, "error": "RuntimeError"}]
    assert revisions(client, bad)["count"] == 0  # the failed event kept its state, nothing half-written
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT raw_hash FROM events WHERE event_id=:i"), {"i": bad}).scalar() == events[2]["raw_hash"]


def test_job_fails_if_the_target_revision_changed(client, events, monkeypatch):
    j = create(client)
    act(client, j, "start")
    monkeypatch.setattr(svc, "active_version", lambda db, adapter_id: "99")
    run_slice(j["id"], T0)
    failed = job(client, j["id"])
    assert failed["status"] == "FAILED" and "no longer the active version" in failed["error"] and failed["processed"] == 0


def test_transitions_are_audited(client, events, users):
    j = create(client, users["analyst"])
    for verb in ("start", "pause", "resume", "cancel"):
        act(client, j, verb, users["analyst"])
    items = client.get(f"{API}/governance/audit", params={"object_type": "replay_job", "object_id": j["id"]},
                       headers=users["admin"]).json()["items"]
    assert [(a["action"], a["decision"]) for a in reversed(items)] == [
        ("REPLAY_JOB_CREATE", "SUCCESS"), ("REPLAY_JOB_START", "SUCCESS"), ("REPLAY_JOB_PAUSE", "SUCCESS"),
        ("REPLAY_JOB_RESUME", "SUCCESS"), ("REPLAY_JOB_CANCEL", "SUCCESS")]
    assert all(a["actor"] == "analyst" for a in items)


# --- > 10,000 events ----------------------------------------------------------------------------------------------


def _bulk(n):
    with TestSessionLocal() as db:
        db.execute(text("""
            INSERT INTO events (event_id, raw_event, raw_hash, received_at, format_detected, status, adapter_id,
                                adapter_version, extensions, processing_metadata, warnings)
            SELECT 'B' || lpad(i::text, 25, '0'), r, encode(sha256(convert_to(r, 'UTF8')), 'hex'),
                   timestamptz '2026-08-01' + i * interval '1 second', 'json', 'SUCCESS', 'json_generic', '1.0',
                   '{}', '{}', '[]'
            FROM generate_series(1, :n) AS i, LATERAL (SELECT '{"user":"u' || i || '","action":"login"}' AS r) x"""),
                   {"n": n})
        db.commit()


def test_replays_over_10000_events_need_governance_and_maker_checker(client, users):
    _bulk(10_001)
    body = {"adapter_id": "json_generic", "reason": "json_generic mapping fix"}
    for headers in (None, users["analyst"]):
        r = client.post(f"{API}/replay/jobs", json=body, headers=headers)
        assert r.status_code == 403 and "10000" in r.json()["error"]["message"]
    r = client.post(f"{API}/replay/jobs", json={**body, "reason": ""}, headers=users["eng1"])
    assert r.status_code == 422
    j = create(client, users["eng1"], **body)
    assert j["total"] == 10_001 and j["status"] == "PENDING_APPROVAL" and j["large_replay"] is True
    r = client.post(f"{API}/replay/jobs/{j['id']}/start", headers=users["eng1"])
    assert r.status_code == 403 and "Maker-checker" in r.json()["error"]["message"]
    assert client.post(f"{API}/replay/jobs/{j['id']}/start").status_code == 403  # anonymous
    assert client.post(f"{API}/replay/jobs/{j['id']}/start", headers=users["analyst"]).status_code == 403
    started = act(client, j, "start", users["eng2"])
    assert started["status"] == "RUNNING" and started["approved_by"] == "eng2"
    act(client, j, "cancel", users["eng2"])
    denied = [a for a in client.get(f"{API}/governance/audit", params={"action": "REPLAY_JOB_START"},
                                    headers=users["admin"]).json()["items"] if a["decision"] == "DENIED"]
    assert len(denied) == 3
    # Exactly 10,000 is not "more than 10,000": normal governed rules (anonymous allowed in permissive mode).
    small = create(client, **body, window_end="2026-08-01T02:46:41+00:00")
    assert small["total"] == 10_000 and small["status"] == "PENDING"


# --- revision-aware rollback --------------------------------------------------------------------------------------


def test_rollback_restores_previous_revision_and_replays_with_evidence(client, events, users):
    drifted = ingest(client, with_added(1))
    client.post(f"{API}/events/{drifted['event_id']}/drift/accept", json={"mode": "add_variant", "note": "ok"},
                headers=users["eng1"])
    ls = client.post(f"{API}/events/{drifted['event_id']}/learning/propose", json={"assistant": "offline"},
                     headers=users["analyst"]).json()
    r = client.post(f"{API}/learning/sessions/{ls['id']}/approve",
                    json={"proposal_version": ls["proposal_version"], "activate": True}, headers=users["eng1"])
    assert r.status_code == 200 and r.json()["target_version"] == 2
    v2 = [ingest(client, with_added(50 + i)) for i in range(3)]
    assert {e["adapter_version"] for e in v2} == {"2"}
    url = f"{API}/replay/rollback/{ADAPTER}"
    assert client.post(url, json={"reason": "bad mapping"}, headers=users["analyst"]).status_code == 403
    assert client.post(url, json={"reason": " "}, headers=users["eng1"]).status_code == 422
    r = client.post(url, json={"reason": "v2 mislabels usernames"}, headers=users["eng1"])
    assert r.status_code == 200, r.text
    rb = r.json()
    assert (rb["rolled_back_version"], rb["restored_version"]) == (2, 1)
    assert rb["before"]["merkle"]["valid"] and rb["after"]["merkle"]["valid"]
    assert rb["before"]["events_per_version"]["2"] == 3 and "phase6 learning rollback" in rb["after"]["path"]
    assert client.get(f"{API}/learning/sessions/{ls['id']}").json()["status"] == "ROLLED_BACK"
    j = rb["replay_job"]
    assert (j["trigger"], j["status"], j["total"], j["selection"]["from_version"], j["target_version"]) == (
        "ROLLBACK", "PENDING", 3, "2", "1")
    act(client, j, "start", users["eng1"])
    done = drive(j["id"])
    assert done["status"] == "COMPLETED" and done["succeeded"] == 3 and done["integrity"]["merkle"]["valid"]
    for e in v2:
        h = revisions(client, e["event_id"])
        assert [(r["trigger"], r["adapter_version"]) for r in h["revisions"]] == [("ORIGINAL", "2"), ("REPLAY", "1")]
        assert h["raw_hash"] == e["raw_hash"] and all(h["integrity"].values())
        with TestSessionLocal() as db:
            row = db.execute(text("SELECT stage_mask, exception_mask FROM event_lineage_compact WHERE event_id=:i"),
                             {"i": e["event_id"]}).one()
        assert {"ROLLBACK", "REPLAY"} <= set(codec.decode(row.stage_mask, row.exception_mask).exceptions)
    audit = client.get(f"{API}/governance/audit", params={"action": "REVISION_ROLLBACK"},
                       headers=users["admin"]).json()["items"]
    ok = next(a for a in audit if a["decision"] == "SUCCESS")
    assert ok["actor"] == "eng1" and ok["details"]["before"]["active_version"] == 2
    assert ok["details"]["replay_job_id"] == j["id"]
    r = client.post(url, json={"reason": "again"}, headers=users["eng1"])
    assert r.status_code == 409 and "no previous approved version" in r.json()["error"]["message"]


# --- manual reprocess + revisions API -----------------------------------------------------------------------------


def test_manual_reprocess_is_revision_tracked(client, events):
    e = events[0]
    assert revisions(client, e["event_id"])["note"].startswith("No revisions")
    for _ in range(2):
        assert client.post(f"{API}/events/{e['event_id']}/reprocess").status_code == 200
    h = revisions(client, e["event_id"])
    assert [(r["revision_no"], r["trigger"]) for r in h["revisions"]] == [(1, "ORIGINAL"), (2, "REPROCESS"), (3, "REPROCESS")]
    assert h["current_revision"] == 3 and all(h["integrity"].values())
    assert client.get(f"{API}/revisions/NOPE").status_code == 404
