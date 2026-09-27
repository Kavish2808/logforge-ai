"""Cold raw vault through ingestion: byte-for-byte recovery, failure never
loses an event, backfill/retry, tamper detection, alerting."""
import hashlib
import os

from sqlalchemy import text

from app.evidence.raw_vault import RawVault, VaultError, set_vault
from app.services import monitor_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest

RAWS = [
    '{"user":"alice","action":"login","note":"ünïcödé ✓ 🚀"}',
    "<34>Oct 11 22:14:15 mymachine su: 'su root' failed for lonvick on /dev/pts/8   ",
    "CEF:0|Security|threatmanager|1.0|100|worm successfully stopped|10|src=10.0.0.1 dst=2.1.2.2 spt=1232",
    "totally unknown \t format with\ttabs and trailing space ",
]


class BrokenVault(RawVault):
    backend = "filesystem"

    def put(self, data):
        raise VaultError("disk full (simulated)")

    def get(self, key):
        raise VaultError("unavailable (simulated)")

    def exists(self, key):
        return False


def test_every_ingested_raw_is_recoverable_byte_for_byte(client, isolated_stores):
    for raw in RAWS:
        e = ingest(client, raw)
        status = client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]
        assert status["status"] == "STORED" and status["tier"] == "HOT_AND_COLD"
        assert status["sha256"] == e["raw_hash"] == hashlib.sha256(raw.encode()).hexdigest()
        assert status["byte_size"] == len(raw.encode())
        assert isolated_stores["vault"].get(status["object_key"]) == raw.encode("utf-8")
        rec = client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()
        assert rec["recovered"] and rec["matches_event_hash"] and rec["matches_hot_copy"]
        assert rec["raw_event"] == raw


def test_failed_events_are_archived_too(client):
    e = ingest(client, "garbage that no parser understands")
    assert e["status"] == "FAILED"
    assert client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]["status"] == "STORED"


def test_vault_failure_never_loses_the_event_and_is_retried(client, isolated_stores):
    set_vault(BrokenVault())
    e = ingest(client, RAWS[0])
    assert e["status"] in ("SUCCESS", "PARTIAL") and e["raw_event"] == RAWS[0]  # event persisted normally
    storage = client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]
    assert storage["status"] == "FAILED" and storage["tier"] == "HOT_ONLY" and "disk full" in storage["error"]
    assert client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()["recovered"] is False
    # The alert sweep raises a RAW_VAULT_FAILURE alert.
    with TestSessionLocal() as db:
        monitor_service.sweep(db)
    alerts = client.get(f"{API}/alerts", params={"kind": "RAW_VAULT_FAILURE"}).json()["items"]
    assert len(alerts) == 1 and alerts[0]["details"]["failed"] == 1
    # Vault restored -> backfill retries and archives the exact bytes.
    set_vault(isolated_stores["vault"])
    r = client.post(f"{API}/integrity/raw/backfill").json()
    assert r["archived"] >= 1 and r["failed"] == 0
    storage = client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]
    assert storage["status"] == "STORED" and storage["attempts"] == 2
    assert client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()["raw_event"] == RAWS[0]


def test_backfill_archives_events_without_a_storage_record(client):
    e = ingest(client, RAWS[1])
    with TestSessionLocal() as db:
        db.execute(text("DELETE FROM event_raw_storage WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    assert client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"] is None
    assert client.get(f"{API}/views/trust").json()["raw_vault"]["not_yet_archived"] == 1
    client.post(f"{API}/integrity/raw/backfill")
    assert client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]["status"] == "STORED"


def test_tampered_cold_copy_is_detected(client, isolated_stores):
    e = ingest(client, RAWS[2])
    key = client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]["object_key"]
    path = isolated_stores["root"].joinpath("vault", *key.split("/"))
    os.chmod(path, 0o644)
    path.write_bytes(b"forged")
    rec = client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()
    assert rec["recovered"] is False and "integrity" in rec["reason"]
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["cold_copy"]["valid"] is False and v["valid"] is False


def test_disabled_vault_is_reported_honestly(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "raw_vault_enabled", False)
    e = ingest(client, RAWS[0])
    storage = client.get(f"{API}/integrity/raw/{e['event_id']}").json()["storage"]
    assert storage["tier"] == "HOT_ONLY" and storage["backend"] == "disabled"
    with TestSessionLocal() as db:
        monitor_service.sweep(db)
    assert client.get(f"{API}/alerts", params={"kind": "RAW_VAULT_FAILURE"}).json()["total"] == 0


def test_trust_view_reports_hot_cold_distribution(client):
    for raw in RAWS[:3]:
        ingest(client, raw)
    vault = client.get(f"{API}/views/trust").json()["raw_vault"]
    assert vault["events_total"] == 3 and vault["hot"] == 3 and vault["cold_stored"] == 3 and vault["cold_failed"] == 0
