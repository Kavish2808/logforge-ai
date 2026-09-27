"""Hash-chained audit log (verification + tamper detection), the alert bus,
and the read-only Phase 7 operational-intelligence view."""
import json

from sqlalchemy import text

from app.alerts.channels import AlertChannel, register_channel
from app.services import alert_service, audit_service, monitor_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest
from tests.integration.test_views_api import snapshot


def record(db, action, i=0):
    return audit_service.record(db, actor=f"user{i}", role="ANALYST", authenticated=True, action=action,
                                object_type="thing", object_id=str(i), details={"i": i, "nested": {"x": [1, 2]}})


# --- audit ----------------------------------------------------------------------------------------------


def test_audit_chain_verifies_and_links(client):
    with TestSessionLocal() as db:
        rows = [(r.previous_hash, r.current_hash) for r in (record(db, "A", i) for i in range(5))]
    assert rows[0][0] == "0" * 64
    assert all(rows[i][0] == rows[i - 1][1] for i in range(1, 5))
    v = client.get(f"{API}/governance/audit/verify").json()
    assert v["valid"] and v["records_checked"] == 5 and v["head_hash"] == rows[-1][1]


def test_modified_record_is_detected(client):
    with TestSessionLocal() as db:
        [record(db, "A", i) for i in range(4)]
        db.execute(text("UPDATE audit_log SET details = '{\"i\": 99}'::jsonb WHERE seq = 2"))
        db.commit()
    v = client.get(f"{API}/governance/audit/verify").json()
    assert v["valid"] is False and v["first_break_seq"] == 2
    assert {"seq": 2, "problem": "CONTENT_MODIFIED", "detail": "record content no longer matches its hash"} in v["problems"]


def test_rehashed_forgery_breaks_the_next_link(client):
    """Editing a record and recomputing its own hash still breaks the chain at the next record."""
    with TestSessionLocal() as db:
        [record(db, "A", i) for i in range(3)]
        row = db.get(audit_service.AuditLog, 2)
        row.actor = "mallory"
        row.current_hash = audit_service.compute_hash(row)
        db.commit()
    v = client.get(f"{API}/governance/audit/verify").json()
    assert v["valid"] is False
    assert not any(p["seq"] == 2 and p["problem"] == "CONTENT_MODIFIED" for p in v["problems"])  # self-consistent...
    assert any(p["seq"] == 3 and p["problem"] == "BROKEN_LINK" for p in v["problems"])


def test_deleted_record_is_detected(client):
    with TestSessionLocal() as db:
        [record(db, "A", i) for i in range(4)]
        db.execute(text("DELETE FROM audit_log WHERE seq = 2"))
        db.commit()
    problems = {p["problem"] for p in client.get(f"{API}/governance/audit/verify").json()["problems"]}
    assert {"SEQUENCE_GAP", "BROKEN_LINK"} <= problems


def test_broken_audit_chain_raises_an_alert(client):
    with TestSessionLocal() as db:
        [record(db, "A", i) for i in range(3)]
        db.execute(text("UPDATE audit_log SET actor='x' WHERE seq=1"))
        db.commit()
        monitor_service.sweep(db)
    assert client.get(f"{API}/alerts", params={"kind": "AUDIT_CHAIN_BROKEN"}).json()["total"] == 1


def test_audit_listing_filters_and_paginates(client):
    with TestSessionLocal() as db:
        [record(db, "A" if i % 2 else "B", i) for i in range(6)]
    page = client.get(f"{API}/governance/audit", params={"limit": 2}).json()
    assert [r["seq"] for r in page["items"]] == [6, 5] and page["next_before_seq"] == 5
    assert all(r["action"] == "B" for r in client.get(f"{API}/governance/audit", params={"action": "B"}).json()["items"])


# --- alerts ----------------------------------------------------------------------------------------------


class Recorder(AlertChannel):
    name = "recorder"

    def __init__(self):
        self.sent = []

    def deliver(self, alert):
        self.sent.append(alert)


class Exploding(AlertChannel):
    name = "exploding"

    def deliver(self, alert):
        raise ConnectionError("destination down")


def event(kind="CRITICAL_DRIFT", key="k1", severity="HIGH"):
    return alert_service.AlertEvent(kind=kind, severity=severity, title="t", message="m", dedup_key=key)


def test_alert_persistence_dedup_ack_and_read(client):
    rec = Recorder()
    register_channel(rec)
    register_channel(Exploding())
    with TestSessionLocal() as db:
        a_id = alert_service.publish(db, event()).id
        b = alert_service.publish(db, event())  # same open dedup key -> same alert
        assert a_id == b.id and b.occurrences == 2
    assert len(rec.sent) == 1  # delivered once, not per repeat
    listed = client.get(f"{API}/alerts").json()
    [alert] = listed["items"]
    assert alert["status"] == "OPEN" and alert["read"] is False and listed["counts"]["unread"] == 1
    deliveries = {d["channel"]: d["ok"] for d in alert["deliveries"]}
    assert deliveries == {"internal": True, "recorder": True, "exploding": False}  # failure recorded, never raised
    assert client.post(f"{API}/alerts/{alert['id']}/read").json()["read"] is True
    assert client.post(f"{API}/alerts/{alert['id']}/read", params={"unread": True}).json()["read"] is False
    acked = client.post(f"{API}/alerts/{alert['id']}/ack", json={"note": "on it"}).json()
    assert acked["status"] == "ACKNOWLEDGED" and acked["acknowledged_by"] == "anonymous"
    assert client.post(f"{API}/alerts/NOPE/ack", json={}).status_code == 404
    # After acknowledgement a repeat opens a new alert.
    with TestSessionLocal() as db:
        c_id = alert_service.publish(db, event()).id
    assert c_id != alert["id"]
    assert client.get(f"{API}/governance/audit", params={"action": "ALERT_ACKNOWLEDGE"}).json()["items"]


def test_invalid_severity_is_rejected():
    with TestSessionLocal() as db:
        try:
            alert_service.publish(db, event(severity="SEVERE"))
        except ValueError:
            return
    raise AssertionError("expected ValueError")


def test_parser_failure_spike_and_overflow_threshold_alerts(client, monkeypatch):
    from app.config import get_settings

    with TestSessionLocal() as db:
        monitor_service.set_thresholds(db, {"parser_failure_min_events": 4, "parser_failure_rate": 0.5,
                                            "overflow_events_threshold": 2}, by="test")
        db.commit()
    for i in range(4):
        ingest(client, f"unparseable junk {i}")
    monkeypatch.setattr(get_settings(), "extension_inline_max_fields", 1)
    for i in range(2):
        ingest(client, json.dumps({"user": "u", "action": "a", "x": i, "y": i, "z": i}))
    r = client.post(f"{API}/alerts/sweep").json()
    assert r["conditions"]["parser_failure_spike"] == 1 and r["conditions"]["extension_overflow"] == 1
    kinds = {a["kind"] for a in client.get(f"{API}/alerts").json()["items"]}
    assert {"PARSER_FAILURE_SPIKE", "EXTENSION_OVERFLOW"} <= kinds


def test_critical_drift_alert(client):
    from tests.integration.phase7.test_sla_confidence import drifted

    drifted(client)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET processing_metadata = jsonb_set(processing_metadata, '{drift,severity}', "
                        "'\"CRITICAL\"') WHERE status='UNDER_REVIEW'"))
        db.commit()
        assert monitor_service.check_critical_drift(db) == 1
        db.commit()
    [a] = client.get(f"{API}/alerts", params={"kind": "CRITICAL_DRIFT"}).json()["items"]
    assert a["severity"] == "CRITICAL" and a["object_id"] == "acmefw_acmefw"


def test_learning_regression_alert(client):
    from tests.integration.phase7.test_sla_confidence import onboard
    from tests.integration.test_learning_flow import approve, base_log, drift_and_accept, propose, with_added

    s = onboard(client)
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    [ingest(client, base_log(100 + i)) for i in range(3)]
    first, _ = drift_and_accept(client, [with_added(i) for i in range(6)])
    ls = propose(client, first["event_id"])
    assert approve(client, ls, activate=True).status_code == 200
    with TestSessionLocal() as db:
        # Simulate production: v1 mostly clean, v2 mostly partial.
        db.execute(text("UPDATE events SET status='SUCCESS' WHERE adapter_id='acmefw_acmefw' AND adapter_version='1'"))
        monitor_service.set_thresholds(db, {"learning_regression_min_events": 2}, by="test")
        db.commit()
    for i in range(3):
        e = ingest(client, with_added(500 + i))
        with TestSessionLocal() as db:
            db.execute(text("UPDATE events SET status='PARTIAL' WHERE event_id=:i"), {"i": e["event_id"]})
            db.commit()
    with TestSessionLocal() as db:
        found = monitor_service.check_learning_regression(db, monitor_service.thresholds(db))
        db.commit()
    assert found == 1
    [a] = client.get(f"{API}/alerts", params={"kind": "LEARNING_REGRESSION"}).json()["items"]
    assert "not automatic" in a["message"]
    # nothing was rolled back automatically
    versions = client.get(f"{API}/onboarding/adapters/acmefw_acmefw").json()["versions"]
    assert [v["status"] for v in versions] == ["SUPERSEDED", "ACTIVE"]


def test_channel_status_never_exposes_destinations(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example/secret-token-123")
    body = client.get(f"{API}/alerts/channels").json()
    assert {"channel": "webhook", "configured": True} in body["channels"]
    assert "secret-token-123" not in json.dumps(body)
    assert "secret-token-123" not in json.dumps(client.get(f"{API}/governance/config").json())


# --- read-only Phase 7 view -----------------------------------------------------------------------------


def test_trust_view_is_read_only_and_complete(client):
    for i in range(3):
        ingest(client, json.dumps({"user": f"u{i}", "action": "x"}))
    client.post(f"{API}/integrity/seal", json={"force": True})
    with TestSessionLocal() as db:
        before = snapshot(db)
        p7_before = db.execute(text("SELECT (SELECT count(*) FROM audit_log), (SELECT count(*) FROM alerts), "
                                    "(SELECT count(*) FROM review_slas), (SELECT count(*) FROM export_log)")).one()
    t = client.get(f"{API}/views/trust").json()
    assert set(t) == {"extension_overflow", "raw_vault", "integrity", "reviews", "confidence", "audit", "alerts", "exports"}
    assert t["integrity"]["events_sealed"] == 3 and t["raw_vault"]["cold_stored"] == 3
    assert t["audit"]["total"] >= 1 and t["audit"]["head"]["seq"] >= 1
    with TestSessionLocal() as db:
        assert snapshot(db) == before
        assert db.execute(text("SELECT (SELECT count(*) FROM audit_log), (SELECT count(*) FROM alerts), "
                               "(SELECT count(*) FROM review_slas), (SELECT count(*) FROM export_log)")).one() == p7_before


def test_lineage_carries_phase7_evidence_without_changing_the_chain(client):
    from tests.integration.test_views_api import STAGES

    e = ingest(client, json.dumps({"user": "u", "action": "x"}))
    client.post(f"{API}/integrity/seal", json={"force": True})
    lin = client.get(f"{API}/views/events/{e['event_id']}/lineage").json()
    assert [s["stage"] for s in lin["chain"]] == STAGES
    ev = lin["evidence"]
    assert ev["extension_storage"]["mode"] == "INLINE" and ev["raw_storage"]["status"] == "STORED"
    assert ev["merkle"]["leaf_index"] == 0


def test_scheduler_tick_runs_every_step(client):
    from app.services import scheduler

    ingest(client, json.dumps({"user": "u", "action": "x"}))
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET received_at = received_at - interval '1 hour'"))
        db.commit()
        result = scheduler.run_once(db)
    assert result["merkle_seal"] == {"batches_sealed": 1}
    assert "error" not in json.dumps(result)
    assert set(result) >= {"raw_vault_backfill", "merkle_seal", "review_sla", "alerts"}
