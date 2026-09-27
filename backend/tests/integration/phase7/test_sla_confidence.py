"""Review SLA (durable deadlines, transitions, escalation, safe fallback) and
the confidence evidence ledger."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services import sla_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest
from tests.integration.test_learning_flow import base_log, with_added

SAMPLES = [base_log(i) for i in range(12)]


def onboard(client):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    assert s["validation"]["result"] == "PASSED"
    return s


def drifted(client):
    s = onboard(client)
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    [ingest(client, base_log(100 + i)) for i in range(5)]
    e = ingest(client, with_added(1))
    assert e["status"] == "UNDER_REVIEW"
    return e


def sweep(at: datetime):
    with TestSessionLocal() as db:
        return sla_service.sweep(db, now=at)


def item(client, item_type):
    return client.get(f"{API}/governance/reviews", params={"item_type": item_type, "refresh": False,
                                                           "include_resolved": True}).json()["items"]


# --- SLA ---------------------------------------------------------------------------------------------


def test_pending_onboarding_gets_a_durable_deadline(client):
    s = onboard(client)
    rows = client.get(f"{API}/governance/reviews", params={"item_type": "ONBOARDING_SESSION"}).json()["items"]
    assert len(rows) == 1 and rows[0]["item_id"] == s["id"] and rows[0]["status"] == "PENDING"
    assert rows[0]["severity"] == "MEDIUM" and rows[0]["sla_hours"] == 72
    assert "approve or reject" in rows[0]["next_action"] and "Nothing is activated on timeout" in rows[0]["fallback"]
    due = rows[0]["due_at"]
    # A policy change does not move an existing durable deadline.
    with TestSessionLocal() as db:
        sla_service.set_policy(db, {"hours": {"MEDIUM": 1}}, by="test")
        db.commit()
    assert client.get(f"{API}/governance/reviews").json()["items"][0]["due_at"] == due


def test_drift_review_transitions_escalates_and_never_auto_promotes(client):
    e = drifted(client)
    now = datetime.now(tz=timezone.utc)
    sweep(now)
    [row] = item(client, "DRIFT_EVENT")
    hours = row["sla_hours"]
    opened = datetime.fromisoformat(row["opened_at"])
    assert row["status"] == "PENDING" and row["item_id"] == e["event_id"]
    assert sweep(opened + timedelta(hours=hours * 0.8))["overdue_alerts"] == 0
    assert item(client, "DRIFT_EVENT")[0]["status"] == "DUE_SOON"
    r = sweep(opened + timedelta(hours=hours * 1.1))
    assert r["overdue_alerts"] == 1 and item(client, "DRIFT_EVENT")[0]["status"] == "OVERDUE"
    r = sweep(opened + timedelta(hours=hours * 2.1))
    row = item(client, "DRIFT_EVENT")[0]
    assert row["status"] == "ESCALATED" and row["escalation_count"] == 2 and r["escalations"] == 1
    assert "ESCALATED to SOC_ADMIN" in row["next_action"]
    assert "No automatic promotion" in row["fallback"] and "v1" in row["fallback"]
    # Safe fallback: nothing changed in the governed state because of the timeout.
    assert client.get(f"{API}/events/{e['event_id']}").json()["status"] == "UNDER_REVIEW"
    versions = client.get(f"{API}/onboarding/adapters/acmefw_acmefw").json()["versions"]
    assert [(v["version"], v["status"]) for v in versions] == [(1, "ACTIVE")]
    kinds = [a["kind"] for a in client.get(f"{API}/alerts").json()["items"]]
    assert "REVIEW_OVERDUE" in kinds and "REVIEW_ESCALATED" in kinds
    # A human decision resolves the SLA (after its deadline).
    client.post(f"{API}/events/{e['event_id']}/drift/accept", json={"mode": "acknowledge"})
    sweep(opened + timedelta(hours=hours * 2.2))
    row = item(client, "DRIFT_EVENT")[0]
    assert row["status"] == "RESOLVED" and row["resolution"] == "DECIDED_AFTER_DEADLINE" and row["next_action"] is None


def test_severity_drives_the_sla_window(client):
    drifted(client)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET processing_metadata = jsonb_set(processing_metadata, '{drift,severity}', "
                        "'\"CRITICAL\"') WHERE status='UNDER_REVIEW'"))
        db.commit()
    sweep(datetime.now(tz=timezone.utc))
    assert item(client, "DRIFT_EVENT")[0]["sla_hours"] == 4 and item(client, "DRIFT_EVENT")[0]["severity"] == "CRITICAL"


def test_learning_sessions_get_slas_from_risk(client):
    e = drifted(client)
    client.post(f"{API}/events/{e['event_id']}/drift/accept", json={"mode": "add_variant"})
    [ingest(client, with_added(i)) for i in range(2, 6)]
    ls = client.post(f"{API}/events/{e['event_id']}/learning/propose", json={"assistant": "offline"}).json()
    sweep(datetime.now(tz=timezone.utc))
    [row] = item(client, "LEARNING_SESSION")
    assert row["item_id"] == ls["id"] and row["severity"] == ls["risk"]


# --- confidence ledger ----------------------------------------------------------------------------------


def test_every_suggestion_is_recorded_with_deterministic_evidence(client):
    s = onboard(client)
    entries = client.get(f"{API}/confidence/onboarding/{s['id']}").json()["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["proposal_version"] == 1 and e["proposal_source"] == "offline" and e["sample_count"] == 12
    ev = e["evidence"]
    assert ev["suggestion"]["overall_confidence"] == e["suggestion_confidence"]
    assert ev["in_sample"]["result"] == "PASSED" and ev["in_sample"]["match_rate"] == 1.0
    assert ev["structural"]["fields_observed"] >= ev["structural"]["fields_mapped"] > 0
    assert ev["holdout"]["evaluated"] and ev["holdout"]["split"] == {"train": 9, "holdout": 3,
                                                                     "rule": "index % 4 == 3 held out"}
    assert ev["holdout"]["rederived_offline"]["result"] == "EVALUATED"
    assert ev["holdout"]["rederived_offline"]["holdout"]["total_samples"] == 3
    mut = ev["mutation"]
    assert mut["evaluated"] and mut["operators"]["field_reorder"]["applicable"]
    assert mut["operators"]["truncate_half"]["kind"] == "fault"
    assert 0 <= mut["robustness_survival_rate"] <= 1 and 0 <= mut["fault_detection_rate"] <= 1
    assert e["human_decision"] is None and e["production_outcome"] is None


def test_ledger_joins_human_decision_and_production_outcome(client):
    s = onboard(client)
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"],
                                                                      "approved_by": "lead"})
    [ingest(client, base_log(200 + i)) for i in range(4)]
    e = client.get(f"{API}/confidence/onboarding/{s['id']}").json()["entries"][0]
    assert e["human_decision"]["action"] == "APPROVED" and e["human_decision"]["by"] == "lead"
    po = e["production_outcome"]
    assert po["measurable"] and po["events"] == 4 and po["by_status"].get("SUCCESS") == 4 and po["success_rate"] == 1.0
    cal = client.get(f"{API}/confidence/calibration").json()
    band = next(b for b in cal["bands"] if b["suggestions"])
    assert band["approved"] == 1 and band["production_events"] == 4 and "not a statistical calibration" in cal["method"]


def test_learning_proposals_are_recorded(client):
    e = drifted(client)
    client.post(f"{API}/events/{e['event_id']}/drift/accept", json={"mode": "add_variant"})
    [ingest(client, with_added(i)) for i in range(2, 6)]
    ls = client.post(f"{API}/events/{e['event_id']}/learning/propose", json={"assistant": "offline"}).json()
    [entry] = client.get(f"{API}/confidence/learning/{ls['id']}").json()["entries"]
    assert entry["subject_type"] == "LEARNING" and entry["evidence"]["holdout"]["kind"].startswith("historical")
    assert entry["evidence"]["suggestion"]["grade_score_note"].startswith("HIGH=0.9")
    assert client.get(f"{API}/confidence/learning/01ARZ3NDEKTSV4RRFFQ69G5FAV").status_code == 404


def test_offline_analyzer_still_works_without_claude(client):
    from app.config import get_settings

    assert get_settings().anthropic_api_key == ""
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}).json()
    r = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "auto"})
    assert r.status_code == 200 and r.json()["proposal_source"] == "offline"
