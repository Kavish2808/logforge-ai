"""Phase 8 Step 3: compact lineage through the H3 persist hook.

Core property: for every supported state,
    detailed lineage -> compact row -> decoded state
is equivalent to the detailed lineage computed at the same moment.
"""
import json

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.phase8 import lineage_codec as codec
from app.phase8 import register as p8
from app.services import scheduler
from app.services.phase8 import compact_lineage_service as svc
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, _phase7_defaults, ingest, isolated_stores  # noqa: F401
from tests.integration.phase8 import format_scenarios as sc
from tests.integration.test_learning_flow import base_log, with_added

SCENARIOS = {
    "json": json.dumps({"user": "alice", "action": "login", "src_ip": "10.0.0.1"}),
    "syslog": "<34>Oct 11 22:14:15 mymachine su: 'su root' failed for lonvick on /dev/pts/8",
    "cef": "CEF:0|Security|threatmanager|1.0|100|worm successfully stopped|10|src=10.0.0.1 dst=2.1.2.2 spt=1232",
    "leef": sc.BARE_LEEF2,
    "xml": sc.BARE_XML,
    "leef_partial": "LEEF:2.0|Acme|GW|1|E|^|src=1.1.1.1^garbage-token^=nokey",
    "garbage": "plain garbage that no parser understands",
    "xxe": '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
}


def row(event_id):
    with TestSessionLocal() as db:
        return db.execute(text("SELECT template_version, stage_mask, exception_mask, is_exception FROM "
                               "event_lineage_compact WHERE event_id=:i"), {"i": event_id}).first()


def detailed_projection(client, event_id):
    return codec.from_detailed_chain(client.get(f"{API}/views/events/{event_id}/lineage").json()["chain"])


def compact(client, event_id, verify=False):
    r = client.get(f"{API}/lineage/compact/{event_id}", params={"verify": verify})
    assert r.status_code == 200, r.text
    return r.json()


def assert_equivalent(client, event_id):
    stored = row(event_id)
    assert stored is not None and stored.template_version == codec.TEMPLATE_VERSION
    decoded = codec.decode(stored.stage_mask, stored.exception_mask, template_version=stored.template_version)
    assert decoded.stages == detailed_projection(client, event_id)
    assert stored.is_exception == decoded.is_exception
    body = compact(client, event_id, verify=True)
    assert body["persisted"] is True and body["verification"]["equivalent"] is True
    return decoded


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_detailed_to_compact_to_reconstructed_is_equivalent(client, name):
    e = ingest(client, SCENARIOS[name])
    decoded = assert_equivalent(client, e["event_id"])
    expected = {"FAILED"} if e["status"] == "FAILED" else {"PARTIAL"} if e["status"] == "PARTIAL" else set()
    assert set(decoded.exceptions) == expected


def test_failed_event_projection(client):
    e = ingest(client, SCENARIOS["garbage"])
    stages = codec.decode(row(e["event_id"]).stage_mask, 0).stages
    assert stages == {"RAW": "OK", "FORMAT": "FAIL", "PARSER": "FAIL", "ADAPTER": "SKIPPED", "NORMALIZATION": "SKIPPED",
                      "FIELD_ACCOUNTING": "SKIPPED", "WARNINGS": stages["WARNINGS"], "DRIFT": "SKIPPED",
                      "BASELINE": "SKIPPED"}
    assert row(e["event_id"]).is_exception


def test_overflow_and_vault_failure_bits(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "extension_inline_max_fields", 1)
    e = ingest(client, json.dumps({"user": "a", "action": "login", "x": 1, "y": 2, "z": 3}))
    assert "OVERFLOW" in assert_equivalent(client, e["event_id"]).exceptions
    monkeypatch.setattr(get_settings(), "raw_vault_enabled", False)
    e = ingest(client, json.dumps({"user": "b", "action": "login"}))
    assert assert_equivalent(client, e["event_id"]).exceptions == ("VAULT_FAILED",)


# --- drift, baseline changes and reprocess ------------------------------------------------------------------


@pytest.fixture()
def onboarded(client, monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85), ("onboarding_min_match_rate", 0.9),
                        ("onboarding_reject_below_match_rate", 0.5), ("onboarding_min_mapping_coverage", 0.3)):
        monkeypatch.setattr(s, name, value)
    sess = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i) for i in range(12)]}).json()
    sess = client.post(f"{API}/onboarding/sessions/{sess['id']}/suggest", json={"provider": "offline"}).json()
    client.post(f"{API}/onboarding/sessions/{sess['id']}/approve", json={"proposal_version": sess["proposal_version"]})
    return [ingest(client, base_log(100 + i)) for i in range(3)]


def test_drift_event_and_point_in_time_semantics(client, onboarded):
    assert_equivalent(client, onboarded[0]["event_id"])  # BASELINE_CREATED
    drifted = ingest(client, with_added(1))
    assert drifted["status"] == "UNDER_REVIEW"
    decoded = assert_equivalent(client, drifted["event_id"])
    assert decoded.stages["DRIFT"] == "WARN" and decoded.stages["BASELINE"] == "WARN"
    assert "DRIFT" in decoded.exceptions
    r = client.post(f"{API}/events/{drifted['event_id']}/drift/accept", json={"mode": "add_variant", "note": "ok"})
    assert r.status_code == 200
    # The stored row is a point-in-time projection; the change is reported, never hidden.
    body = compact(client, drifted["event_id"], verify=True)
    assert body["verification"]["stale"] is True
    assert {"stage": "BASELINE", "compact": "WARN", "detailed_now": "OK"} in body["verification"]["stage_differences"]
    # Reprocessing recomputes the row from the reprocessed fields (never stale ones).
    assert client.post(f"{API}/events/{drifted['event_id']}/reprocess").status_code == 200
    after = assert_equivalent(client, drifted["event_id"])
    assert after.stages["BASELINE"] == "OK"


def test_service_bits_survive_recomputation(client):
    e = ingest(client, SCENARIOS["json"])
    with TestSessionLocal() as db:
        svc.mark_exception(db, [e["event_id"]], "REPLAY")
        db.commit()
        with pytest.raises(ValueError):
            svc.mark_exception(db, [e["event_id"]], "FAILED")  # derived bits are not service-owned
    assert "REPLAY" in codec.decode(row(e["event_id"]).stage_mask, row(e["event_id"]).exception_mask).exceptions
    assert client.post(f"{API}/events/{e['event_id']}/reprocess").status_code == 200
    r = row(e["event_id"])
    assert codec.decode(r.stage_mask, r.exception_mask).exceptions == ("REPLAY",) and r.is_exception
    assert compact(client, e["event_id"], verify=True)["verification"]["equivalent"] is True


def test_reprocess_rollback_does_not_leak_pending_work(client):
    e = ingest(client, SCENARIOS["json"])
    with TestSessionLocal() as db:
        from app.db.models.event import Event

        event = db.get(Event, e["event_id"])
        svc.persist_hook(db, event, True)
        db.rollback()
        assert svc._PENDING not in db.info
        db.commit()  # nothing pending: no error, no write


# --- failure isolation, backfill, API ----------------------------------------------------------------------


def test_hook_failure_never_loses_the_event_and_backfill_repairs(client, monkeypatch):
    monkeypatch.setattr(svc, "compute", lambda db, e, **kw: 1 / 0)
    e = ingest(client, SCENARIOS["json"])
    assert e["status"] == "SUCCESS" and row(e["event_id"]) is None
    monkeypatch.undo()
    body = compact(client, e["event_id"])
    assert body["persisted"] is False and "computed on read" in body["note"] and body["decodable"] is True
    with TestSessionLocal() as db:
        result = scheduler.run_once(db)
    assert result["compact_lineage_backfill"]["written"] >= 1
    assert_equivalent(client, e["event_id"])


def test_corrupted_row_is_reported_not_guessed(client):
    e = ingest(client, SCENARIOS["json"])
    with TestSessionLocal() as db:
        db.execute(text("UPDATE event_lineage_compact SET stage_mask = stage_mask | (1::bigint << 30) WHERE event_id=:i"),
                   {"i": e["event_id"]})
        db.commit()
    body = compact(client, e["event_id"])
    assert body["decodable"] is False and "reserved bits" in body["error"]
    with TestSessionLocal() as db:
        db.execute(text("UPDATE event_lineage_compact SET template_version = 2 WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    assert "mismatch" in compact(client, e["event_id"])["error"]


def test_unknown_event_is_404(client):
    assert client.get(f"{API}/lineage/compact/NOPE").status_code == 404


def test_detailed_lineage_api_is_unchanged(client):
    e = ingest(client, SCENARIOS["json"])
    lin = client.get(f"{API}/views/events/{e['event_id']}/lineage").json()
    assert [c["stage"] for c in lin["chain"]] == ["RAW", "FORMAT_DETECTION", "PARSER", "ADAPTER", "NORMALIZATION",
                                                  "FIELD_ACCOUNTING", "WARNINGS", "DRIFT_DECISION", "BASELINE",
                                                  "LEARNING_HISTORY"]
    assert set(lin) == {"event_id", "status", "chain", "integrity", "field_accounting", "nothing_silently_discarded",
                        "basis", "evidence"}


def test_stats_and_storage_benchmark(client):
    for name in ("json", "garbage", "leef", "xml"):
        ingest(client, SCENARIOS[name])
    stats = client.get(f"{API}/lineage/compact/stats").json()
    assert stats["rows"] == stats["events_total"] == 4 and stats["missing_rows"] == 0
    assert stats["by_exception"]["FAILED"] == 1 and stats["by_template_version"] == {"1": 4}
    assert sum(stats["by_stage_outcome"]["RAW"].values()) == 4
    bench = client.get(f"{API}/lineage/compact/benchmark", params={"sample": 4}).json()
    assert bench["sample"] == 4 and bench["compact"]["packed_bytes"] == 5
    assert bench["compact"]["column_payload_bytes_avg"] <= 16  # smallint + bigint + integer + boolean
    assert bench["compact"]["row_bytes_avg"] < bench["detailed"]["jsonb_bytes_avg"]
    assert bench["ratio_detailed_jsonb_to_compact_row"] > 5


def test_phase8_disabled_writes_no_rows(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "phase8_enabled", False)
    p8.register_all()
    try:
        e = ingest(client, SCENARIOS["json"])
        assert row(e["event_id"]) is None
    finally:
        monkeypatch.undo()
        p8.register_all()
