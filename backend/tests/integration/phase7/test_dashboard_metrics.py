"""Operational dashboard reflects stored data (E2E defect D5): throughput read a missing key and was
always 0, integrity_failures and the recent-event revision were hardcoded."""
import pytest
from sqlalchemy import text

from app.api.routes import integrations
from app.services import monitor_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(integrations, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(integrations, "_DATA_PATH", str(tmp_path / "integrations.json"))


def dashboard(client):
    r = client.get(f"{API}/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


def sql(statement, **params):
    with TestSessionLocal() as db:
        db.execute(text(statement), params)
        db.commit()


def test_empty_database_is_all_zero_not_fabricated(client):
    d = dashboard(client)
    assert d["counts"] == {"events": 0, "parsed": 0, "partial": 0, "failed": 0, "drifts": 0, "pending_reviews": 0,
                           "integrity_failures": 0, "integrations": 0}
    assert d["throughput"] == [] and d["recent_events"] == [] and d["recent_alerts"] == []


def test_one_event(client):
    e = ingest(client, '{"user":"a","action":"login"}')
    d = dashboard(client)
    assert d["counts"]["events"] == 1 and d["counts"]["parsed"] == 1
    assert [p["count"] for p in d["throughput"]] == [1]
    assert d["recent_events"][0]["id"] == e["event_id"] and d["recent_events"][0]["revision"] == 1


def test_multiple_events_across_hours_and_sources(client):
    json_ids = [ingest(client, f'{{"user":"u{i}","action":"login"}}')["event_id"] for i in range(5)]
    ingest(client, "CEF:0|Security|threatmanager|1.0|100|worm stopped|10|src=10.0.0.1 dst=2.1.2.2")
    ingest(client, "not a known format at all")
    sql("UPDATE events SET received_at = date_trunc('hour', now()) - interval '2 hours' WHERE event_id = ANY(:ids)",
        ids=json_ids[:3])
    d = dashboard(client)
    assert d["counts"] == {**d["counts"], "events": 7, "parsed": 6, "failed": 1}
    assert sorted(p["count"] for p in d["throughput"]) == [3, 4]  # per-hour totals, not zero
    assert sum(p["count"] for p in d["throughput"]) == 7
    with TestSessionLocal() as db:
        per_hour = dict(db.execute(text("SELECT date_trunc('hour', received_at), count(*) FROM events GROUP BY 1")).all())
    assert sorted(per_hour.values()) == sorted(p["count"] for p in d["throughput"])
    formats = {f["name"]: f["count"] for f in d["formats"]}
    assert formats == {"JSON": 5, "CEF": 1, "UNKNOWN": 1}


def test_recent_event_revision_is_the_current_revision(client):
    e = ingest(client, '{"user":"a","action":"login"}')
    assert client.post(f"{API}/events/{e['event_id']}/reprocess").status_code == 200
    assert client.post(f"{API}/events/{e['event_id']}/reprocess").status_code == 200
    assert dashboard(client)["recent_events"][0]["revision"] == 3


def test_integrity_failures_come_from_open_integrity_alerts(client):
    e = ingest(client, '{"user":"a","action":"login"}')
    assert client.post(f"{API}/integrity/seal", json={"force": True}).status_code == 200
    sql("UPDATE events SET raw_event = raw_event || 'x' WHERE event_id=:i", i=e["event_id"])
    assert dashboard(client)["counts"]["integrity_failures"] == 0  # not yet detected by a sweep
    with TestSessionLocal() as db:
        monitor_service.sweep(db)
    d = dashboard(client)
    assert d["counts"]["integrity_failures"] == 1 and "integrity/verify" in d["integrity_failures_basis"]
    [alert] = client.get(f"{API}/alerts", params={"kind": "INTEGRITY_FAILURE"}).json()["items"]
    assert client.post(f"{API}/alerts/{alert['id']}/ack", json={}).status_code == 200
    assert dashboard(client)["counts"]["integrity_failures"] == 0  # acknowledged -> no longer open


def test_integration_count_is_the_configured_integrations(client, users):
    for i in range(3):
        r = client.post(f"{API}/integrations", json={"name": f"s{i}", "url": "http://93.184.216.34/h"},
                        headers=users["admin"])
        assert r.status_code == 201
    assert dashboard(client)["counts"]["integrations"] == 3
