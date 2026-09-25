"""Phase 5 drift detection, end to end against the full FastAPI app + real Postgres."""
import hashlib

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db.repository import baseline_repo
from app.services import drift_service
from tests.conftest import load_fixture

INGEST = "/api/v1/ingest"

# Palo Alto CEF with the fixture's structure (values differ only).
PA_SAME_STRUCTURE = (
    "<134>Jan 19 08:00:00 PA-VM2 CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|3|"
    "src=10.9.9.9 dst=1.1.1.1 spt=40000 dpt=80 proto=udp act=deny suser=asmith cs1Label=URL cs1=example.org"
)
# Same vendor/adapter, but the extension structure changed: suser/cs1Label/cs1
# removed, five new keys added.
PA_DRIFTED = (
    "<134>Jan 19 08:05:00 PA-VM CEF:0|Palo Alto Networks|PAN-OS|11.0.0|traffic|THREAT|5|"
    "rt=1705650300000 src=10.0.0.30 dst=93.184.216.34 spt=51500 dpt=443 proto=tcp act=allow "
    "deviceExternalId=0123456789 cs2Label=Zone cs2=trust cn1=42"
)


def ingest(client, raw: str) -> dict:
    resp = client.post(INGEST, json={"raw_log": raw})
    assert resp.status_code == 201
    return resp.json()


def drift_of(body: dict) -> dict | None:
    return body["processing_metadata"].get("drift")


@pytest.fixture()
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "drift_enabled", True)
    monkeypatch.setattr(s, "drift_similarity_threshold", 0.85)
    return s


@pytest.fixture()
def pa_baseline(client, settings):
    """Bootstraps the paloalto_cef baseline from the standard fixture."""
    return ingest(client, load_fixture("cef_samples/paloalto.txt"))


# --- baseline bootstrap & normal flow ------------------------------------------


def test_first_vendor_event_bootstraps_baseline_without_changing_status(client, pa_baseline):
    assert pa_baseline["status"] == "SUCCESS"
    drift = drift_of(pa_baseline)
    assert drift["status"] == "BASELINE_CREATED"
    assert drift["source_key"] == "paloalto_cef"
    assert drift["baseline_origin"] == "auto_bootstrap"
    assert drift["baseline_version"] == 1

    baseline = client.get("/api/v1/drift/baselines/paloalto_cef").json()
    assert baseline["origin"] == "auto_bootstrap"
    assert baseline["created_from_event_id"] == pa_baseline["event_id"]
    assert baseline["fingerprint"]["signature"] == pa_baseline["structural_fingerprint"]["signature"]
    assert baseline["accepted_variants"] == []


def test_same_structure_is_normal(client, pa_baseline):
    body = ingest(client, PA_SAME_STRUCTURE)
    assert body["status"] == "SUCCESS"
    drift = drift_of(body)
    assert drift["status"] == "NORMAL"
    assert drift["similarity"] == 1.0
    assert drift["threshold"] == 0.85
    assert drift["reonboarding_required"] is False


# --- drift ------------------------------------------------------------------------


def test_drifted_event_is_under_review_and_fully_preserved(client, pa_baseline):
    body = ingest(client, PA_DRIFTED)

    assert body["status"] == "UNDER_REVIEW"
    # nothing discarded
    assert body["raw_event"] == PA_DRIFTED
    assert body["raw_hash"] == hashlib.sha256(PA_DRIFTED.encode("utf-8")).hexdigest()
    assert body["vendor"] == "Palo Alto Networks"
    assert body["adapter_id"] == "paloalto_cef"
    assert body["normalized_event"] is not None
    assert body["network"]["src_ip"] == "10.0.0.30"
    assert body["extensions"]["deviceExternalId"] == "0123456789"
    fp = body["structural_fingerprint"]
    assert "deviceExternalId" in fp["field_set"]
    assert fp["field_types"]["cn1"] == "string"

    drift = drift_of(body)
    assert drift["status"] == "DRIFT"
    assert drift["similarity"] < drift["threshold"] == 0.85
    assert drift["original_status"] == "SUCCESS"
    assert drift["reonboarding_required"] is True
    assert "paloalto_cef" in drift["recommended_action"]
    diff = drift["differences"]
    assert diff["removed_fields"] == ["cs1", "cs1Label", "suser"]
    assert diff["added_fields"] == ["cn1", "cs2", "cs2Label", "deviceExternalId", "rt"]
    assert diff["field_count"] == {"baseline": 17, "current": 19}
    assert set(drift["components"]) == {"field_set", "field_order", "field_count", "field_types"}

    # persisted, retrievable, and in the review queue
    fetched = client.get(f"/api/v1/events/{body['event_id']}").json()
    assert fetched["status"] == "UNDER_REVIEW"
    assert fetched["raw_event"] == PA_DRIFTED
    queue = client.get("/api/v1/events", params={"status": "UNDER_REVIEW", "adapter_id": "paloalto_cef"}).json()
    assert [e["event_id"] for e in queue["items"]] == [body["event_id"]]

    # baseline is never changed automatically
    baseline = client.get("/api/v1/drift/baselines/paloalto_cef").json()
    assert baseline["version"] == 1
    assert baseline["under_review_count"] == 1


def test_original_partial_status_is_recorded(client, pa_baseline):
    raw = PA_DRIFTED.replace("11.0.0", "9" * 200)  # device_version truncation -> PARTIAL
    body = ingest(client, raw)
    assert body["status"] == "UNDER_REVIEW"
    assert drift_of(body)["original_status"] == "PARTIAL"
    assert any("truncated" in w for w in body["warnings"])


def test_threshold_is_configurable(client, pa_baseline, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_similarity_threshold", 0.0)
    assert ingest(client, PA_DRIFTED)["status"] == "SUCCESS"

    monkeypatch.setattr(settings, "drift_similarity_threshold", 1.0)
    one_extra = PA_SAME_STRUCTURE + " cn1=1"
    body = ingest(client, one_extra)
    assert body["status"] == "UNDER_REVIEW"
    assert drift_of(body)["threshold"] == 1.0


def test_drift_disabled_restores_phase_0_4_behavior(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_enabled", False)
    ingest(client, load_fixture("cef_samples/paloalto.txt"))
    body = ingest(client, PA_DRIFTED)
    assert body["status"] == "SUCCESS"
    assert drift_of(body) is None
    assert client.get("/api/v1/drift/baselines").json()["total"] == 0


# --- exclusions ---------------------------------------------------------------------


def test_generic_adapters_are_never_evaluated(client, settings):
    a = ingest(client, load_fixture("json_samples/valid_login.json"))
    b = ingest(client, '{"completely": "different", "structure": [1, 2], "x": {"y": 1}}')
    c = ingest(client, load_fixture("cef_samples/valid_generic.txt"))
    for body in (a, b, c):
        assert body["status"] == "SUCCESS"
        assert drift_of(body) is None
    assert client.get("/api/v1/drift/baselines").json()["total"] == 0


def test_failed_events_are_untouched(client, settings):
    body = ingest(client, "not a recognizable log at all")
    assert body["status"] == "FAILED"
    assert drift_of(body) is None
    body = ingest(client, load_fixture("cef_samples/malformed.txt"))
    assert body["status"] == "FAILED"
    assert drift_of(body) is None
    assert client.get("/api/v1/drift/baselines").json()["total"] == 0


def test_batch_reports_under_review_count(client, pa_baseline):
    logs = [
        {"raw_log": PA_SAME_STRUCTURE},
        {"raw_log": PA_DRIFTED},
        {"raw_log": "garbage"},
        {"raw_log": load_fixture("json_samples/valid_login.json")},
    ]
    body = client.post("/api/v1/ingest/batch", json={"logs": logs}).json()
    assert body["total"] == 4
    assert body["success_count"] == 2
    assert body["partial_count"] == 0
    assert body["failed_count"] == 1
    assert body["under_review_count"] == 1


# --- failure containment -----------------------------------------------------------


def test_comparator_failure_never_fails_the_event(client, pa_baseline, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated comparator bug")

    monkeypatch.setattr(drift_service, "compare", boom)
    body = ingest(client, PA_DRIFTED)
    assert body["status"] == "SUCCESS"
    assert body["raw_event"] == PA_DRIFTED
    assert body["normalized_event"] is not None
    drift = drift_of(body)
    assert drift["status"] == "ERROR"
    assert "RuntimeError" in drift["error"]


def test_database_error_in_drift_is_contained_by_savepoint(client, settings, monkeypatch):
    def broken_get_baseline(db, source_key):
        db.execute(text("SELECT * FROM table_that_does_not_exist"))

    monkeypatch.setattr(baseline_repo, "get_baseline", broken_get_baseline)
    raw = load_fixture("cef_samples/paloalto.txt")
    body = ingest(client, raw)
    assert body["status"] == "SUCCESS"
    assert drift_of(body)["status"] == "ERROR"
    # the event itself was committed despite the failed SQL inside drift evaluation
    assert client.get(f"/api/v1/events/{body['event_id']}").json()["raw_event"] == raw


# --- human review --------------------------------------------------------------------


def test_accept_add_variant_restores_status_and_future_events_are_normal(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    resp = client.post(
        f"/api/v1/events/{drifted['event_id']}/drift/accept",
        json={"mode": "add_variant", "note": "PAN-OS 11 upgrade"},
    )
    assert resp.status_code == 200
    body = resp.json()

    event = body["event"]
    assert event["status"] == "SUCCESS"
    assert event["raw_event"] == PA_DRIFTED
    drift = drift_of(event)
    assert drift["status"] == "DRIFT"  # history is kept truthful
    assert drift["reonboarding_required"] is False
    assert drift["review"]["resolution"] == "accepted_variant"
    assert drift["review"]["note"] == "PAN-OS 11 upgrade"
    assert drift["review"]["baseline_version"] == 2

    baseline = body["baseline"]
    assert baseline["version"] == 2
    assert baseline["origin"] == "auto_bootstrap"  # reference itself unchanged
    assert len(baseline["accepted_variants"]) == 1
    assert baseline["accepted_variants"][0]["accepted_from_event_id"] == drifted["event_id"]
    assert baseline["under_review_count"] == 0

    again = ingest(client, PA_DRIFTED.replace("cn1=42", "cn1=7"))
    assert again["status"] == "SUCCESS"
    assert drift_of(again)["matched"] == "variant:0"
    # the original structure is still accepted too
    assert ingest(client, PA_SAME_STRUCTURE)["status"] == "SUCCESS"


def test_accept_replace_baseline(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    resp = client.post(f"/api/v1/events/{drifted['event_id']}/drift/accept", json={"mode": "replace_baseline"})
    assert resp.status_code == 200
    baseline = resp.json()["baseline"]
    assert baseline["origin"] == "human_review"
    assert baseline["version"] == 2
    assert baseline["created_from_event_id"] == drifted["event_id"]
    assert baseline["accepted_variants"] == []

    # the old structure is now the drift
    assert ingest(client, PA_SAME_STRUCTURE)["status"] == "UNDER_REVIEW"


def test_duplicate_variant_is_not_added_twice(client, pa_baseline):
    first = ingest(client, PA_DRIFTED)
    second = ingest(client, PA_DRIFTED)
    client.post(f"/api/v1/events/{first['event_id']}/drift/accept", json={"mode": "add_variant"})
    resp = client.post(f"/api/v1/events/{second['event_id']}/drift/accept", json={"mode": "add_variant"})
    assert resp.status_code == 200
    assert resp.json()["event"]["status"] == "SUCCESS"
    assert len(resp.json()["baseline"]["accepted_variants"]) == 1
    assert resp.json()["baseline"]["version"] == 2


def test_accept_requires_drifted_event(client, pa_baseline):
    resp = client.post(f"/api/v1/events/{pa_baseline['event_id']}/drift/accept", json={"mode": "add_variant"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"

    drifted = ingest(client, PA_DRIFTED)
    client.post(f"/api/v1/events/{drifted['event_id']}/drift/accept", json={"mode": "add_variant"})
    again = client.post(f"/api/v1/events/{drifted['event_id']}/drift/accept", json={"mode": "add_variant"})
    assert again.status_code == 409


def test_accept_unknown_event_is_404(client):
    resp = client.post("/api/v1/events/01ARZ3NDEKTSV4RRFFQ69G5FAV/drift/accept", json={"mode": "add_variant"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_accept_validates_mode(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    resp = client.post(f"/api/v1/events/{drifted['event_id']}/drift/accept", json={"mode": "auto_fix"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# --- reprocess -------------------------------------------------------------------------


def test_reprocess_reevaluates_drift_and_keeps_review_history(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    event_id = drifted["event_id"]

    # still drifted on reprocess while nobody has reviewed it
    body = client.post(f"/api/v1/events/{event_id}/reprocess").json()["event"]
    assert body["status"] == "UNDER_REVIEW"
    assert body["raw_event"] == PA_DRIFTED

    client.post(f"/api/v1/events/{event_id}/drift/accept", json={"mode": "add_variant", "note": "ok"})
    body = client.post(f"/api/v1/events/{event_id}/reprocess").json()["event"]
    assert body["status"] == "SUCCESS"
    drift = drift_of(body)
    assert drift["status"] == "NORMAL"
    assert drift["matched"] == "variant:0"
    assert drift["review"]["note"] == "ok"


def test_other_queued_events_resolve_via_reprocess_after_accept(client, pa_baseline):
    first = ingest(client, PA_DRIFTED)
    second = ingest(client, PA_DRIFTED.replace("cn1=42", "cn1=43"))
    assert second["status"] == "UNDER_REVIEW"
    client.post(f"/api/v1/events/{first['event_id']}/drift/accept", json={"mode": "add_variant"})
    body = client.post(f"/api/v1/events/{second['event_id']}/reprocess").json()["event"]
    assert body["status"] == "SUCCESS"


# --- baselines API -------------------------------------------------------------------


def test_list_and_get_baselines(client, settings):
    ingest(client, load_fixture("cef_samples/paloalto.txt"))
    ingest(client, load_fixture("syslog_samples/cisco_asa.txt"))
    ingest(client, load_fixture("syslog_samples/fortinet.txt"))

    body = client.get("/api/v1/drift/baselines").json()
    assert body["total"] == 3
    assert [b["source_key"] for b in body["items"]] == ["cisco_asa", "fortinet", "paloalto_cef"]
    assert all(b["origin"] == "auto_bootstrap" for b in body["items"])

    one = client.get("/api/v1/drift/baselines/cisco_asa").json()
    assert one["adapter_id"] == "cisco_asa"
    assert one["format_detected"] == "syslog"


def test_get_unknown_baseline_is_404(client):
    resp = client.get("/api/v1/drift/baselines/no_such_source")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
