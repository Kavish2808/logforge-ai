"""Integration tests for the Phase 0-4 hardening pass: request-boundary
validation, defensive persistence guards, and the consistent error
envelope. These exercise real failure paths through the full API + DB,
not just the unit-level building blocks.
"""


def test_error_envelope_shape_on_405_method_not_allowed(client):
    # Regression test: 405 is raised by Starlette's routing layer itself
    # (as the base starlette.exceptions.HTTPException, not the
    # fastapi.HTTPException subclass our routes raise), so it needs its
    # own check that it still goes through our envelope, not Starlette's
    # raw {"detail": ...} default.
    resp = client.delete("/api/v1/ingest")
    assert resp.status_code == 405
    body = resp.json()
    assert body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_error_envelope_shape_on_404_unmatched_route(client):
    resp = client.get("/api/v1/this-route-does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"


def test_error_envelope_shape_on_404(client):
    resp = client.get("/api/v1/events/01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert isinstance(body["error"]["message"], str)


def test_error_envelope_shape_on_validation_error(client):
    resp = client.post("/api/v1/ingest", json={"raw_log": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "raw_log" in body["error"]["fields"]


def test_raw_log_rejects_nul_bytes(client):
    resp = client.post("/api/v1/ingest", json={"raw_log": "hello\x00world"})
    assert resp.status_code == 422
    assert "NUL" in resp.json()["error"]["fields"]["raw_log"]


def test_raw_log_over_max_length_is_rejected(client):
    oversized = "a" * 300_000
    resp = client.post("/api/v1/ingest", json={"raw_log": oversized})
    assert resp.status_code == 422


def test_batch_over_max_size_is_rejected(client):
    logs = [{"raw_log": "x"} for _ in range(1001)]
    resp = client.post("/api/v1/ingest/batch", json={"logs": logs})
    assert resp.status_code == 422


def test_batch_empty_list_is_rejected(client):
    resp = client.post("/api/v1/ingest/batch", json={"logs": []})
    assert resp.status_code == 422


def test_oversized_product_version_is_truncated_not_a_server_error(client):
    # device_version (-> product_version, VARCHAR(64)) is attacker/vendor
    # controlled content, not adapter-authored — it must never be able to
    # turn into an unhandled DB error.
    huge_version = "9" * 200
    raw = f"CEF:0|Palo Alto Networks|PAN-OS|{huge_version}|traffic|THREAT|5|src=10.0.0.1"
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PARTIAL"
    assert len(body["product_version"]) == 64
    assert any("product_version" in w and "truncated" in w for w in body["warnings"])


def test_oversized_severity_is_truncated_not_a_server_error(client):
    huge_severity = "X" * 100
    raw = f"CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|{huge_severity}|src=10.0.0.1"
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PARTIAL"
    assert len(body["severity"]) == 32
    assert any("severity" in w and "truncated" in w for w in body["warnings"])


def test_adapter_filter_on_events_list(client):
    client.post(
        "/api/v1/ingest",
        json={"raw_log": "<166>Jan 18 12:05:00 ciscoasa %ASA-6-302013: test connection"},
    )
    resp = client.get("/api/v1/events", params={"adapter_id": "cisco_asa"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["adapter_id"] == "cisco_asa" for item in body["items"])
