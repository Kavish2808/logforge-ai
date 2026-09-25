"""End-to-end tests against the full FastAPI app + real Postgres database."""
import hashlib

from tests.conftest import load_fixture


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingest_json_log_success(client):
    raw = load_fixture("json_samples/valid_login.json")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    body = resp.json()

    assert body["status"] == "SUCCESS"
    assert body["format_detected"] == "json"
    assert body["vendor"] == "Generic"
    assert body["network"]["src_ip"] == "10.0.0.5"
    assert body["user"]["name"] == "jdoe"
    assert body["raw_event"] == raw
    assert body["raw_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(body["event_id"]) == 26


def test_ingest_syslog_cisco_asa(client):
    raw = load_fixture("syslog_samples/cisco_asa.txt")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    body = resp.json()

    assert body["format_detected"] == "syslog"
    assert body["vendor"] == "Cisco"
    assert body["product"] == "ASA"
    assert body["adapter_id"] == "cisco_asa"
    assert body["ocsf_class_name"] == "Network Activity"


def test_ingest_syslog_fortinet_maps_network_fields(client):
    raw = load_fixture("syslog_samples/fortinet.txt")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    body = resp.json()

    assert body["vendor"] == "Fortinet"
    assert body["adapter_id"] == "fortinet"
    assert body["network"]["src_ip"] == "10.0.0.15"
    assert body["network"]["dst_ip"] == "8.8.8.8"
    assert body["network"]["dst_port"] == 53  # coerced to int by field_map type


def test_ingest_cef_paloalto(client):
    raw = load_fixture("cef_samples/paloalto.txt")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    body = resp.json()

    assert body["format_detected"] == "cef"
    assert body["vendor"] == "Palo Alto Networks"
    assert body["adapter_id"] == "paloalto_cef"
    assert body["network"]["src_ip"] == "10.0.0.30"
    assert body["product_version"] == "10.2.0"


def test_unknown_field_preservation_in_json(client):
    raw = load_fixture("json_samples/valid_unknown_fields.json")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    body = resp.json()

    assert body["extensions"]["custom_field_one"] == "abc"
    assert body["extensions"]["custom_nested"] == {"a": 1, "b": 2}
    assert body["extensions"]["custom_list"] == [1, 2, 3]
    # fields that WERE mapped must not also leak into extensions
    assert "message" not in body["extensions"]
    assert "severity" not in body["extensions"]


def test_unknown_format_is_marked_failed_but_raw_is_preserved(client):
    raw = "this is definitely not syslog, json, or cef"
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    body = resp.json()

    assert body["status"] == "FAILED"
    assert body["format_detected"] == "unknown"
    assert body["raw_event"] == raw
    assert body["raw_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert body["error_message"] is not None


def test_malformed_known_format_is_preserved_as_failed(client):
    raw = load_fixture("cef_samples/malformed.txt")
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    body = resp.json()

    assert body["status"] == "FAILED"
    assert body["raw_event"] == raw
    assert body["error_message"] is not None

    # visible through GET /events and reprocessable
    event_id = body["event_id"]
    get_resp = client.get(f"/api/v1/events/{event_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "FAILED"


def test_event_is_persisted_and_retrievable_by_id(client):
    raw = load_fixture("json_samples/valid_login.json")
    ingest_resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    event_id = ingest_resp.json()["event_id"]

    get_resp = client.get(f"/api/v1/events/{event_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["event_id"] == event_id
    assert get_resp.json()["raw_event"] == raw


def test_get_nonexistent_event_returns_404(client):
    resp = client.get("/api/v1/events/01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert resp.status_code == 404


def test_reprocess_event_updates_it_using_current_adapters(client):
    raw = load_fixture("syslog_samples/cisco_asa.txt")
    ingest_resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    event_id = ingest_resp.json()["event_id"]

    reprocess_resp = client.post(f"/api/v1/events/{event_id}/reprocess")
    assert reprocess_resp.status_code == 200
    body = reprocess_resp.json()
    assert body["reprocessed"] is True
    assert body["event"]["event_id"] == event_id
    assert body["event"]["vendor"] == "Cisco"
    # raw_event / raw_hash / received_at must never change on reprocess
    assert body["event"]["raw_event"] == raw


def test_batch_ingestion_processes_each_log_independently(client):
    logs = [
        {"raw_log": load_fixture("json_samples/valid_login.json")},
        {"raw_log": "not a recognizable log format at all"},
        {"raw_log": load_fixture("cef_samples/valid_generic.txt")},
    ]
    resp = client.post("/api/v1/ingest/batch", json={"logs": logs})
    assert resp.status_code == 201
    body = resp.json()

    assert body["total"] == 3
    assert body["success_count"] == 2
    assert body["failed_count"] == 1
    assert len(body["results"]) == 3
    statuses = {r["status"] for r in body["results"]}
    assert "FAILED" in statuses
    assert "SUCCESS" in statuses


def test_list_events_filters_by_status(client):
    client.post("/api/v1/ingest", json={"raw_log": load_fixture("json_samples/valid_login.json")})
    client.post("/api/v1/ingest", json={"raw_log": "unrecognizable garbage log line"})

    resp = client.get("/api/v1/events", params={"status": "FAILED"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["status"] == "FAILED" for item in body["items"])


def test_list_events_pagination(client):
    for _ in range(3):
        client.post("/api/v1/ingest", json={"raw_log": load_fixture("json_samples/valid_login.json")})

    resp = client.get("/api/v1/events", params={"limit": 2, "offset": 0})
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["total"] >= 3
