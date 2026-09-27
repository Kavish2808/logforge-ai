"""Adaptive extension spill through the real pipeline: zero loss, field
accounting, recovery, reprocessing, operational stats and onboarding evidence."""
import json

from sqlalchemy import text

from app.config import get_settings
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest


def wide_json(i: int, n: int = 40) -> str:
    return json.dumps({"user": f"u{i}", "action": "login", **{f"custom_attr_{k:03d}": f"value-{i}-{k}" for k in range(n)}})


def spill_budget(monkeypatch, fields=5, size=100_000):
    s = get_settings()
    monkeypatch.setattr(s, "extension_inline_max_fields", fields)
    monkeypatch.setattr(s, "extension_inline_max_bytes", size)


def parsed_fields(raw: str) -> dict:
    return json.loads(raw)


def test_normal_event_stays_inline(client):
    e = ingest(client, json.dumps({"user": "a", "action": "login", "extra": 1}))
    assert (e["processing_metadata"].get("extension_spill") or None) is None
    view = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()
    assert view["mode"] == "INLINE" and view["overflow_field_count"] == 0


def test_spilled_event_preserves_every_field_and_hash(client, monkeypatch):
    spill_budget(monkeypatch)
    raw = wide_json(1)
    e = ingest(client, raw)
    spill = e["processing_metadata"]["extension_spill"]
    assert spill["mode"] == "SPILLED" and spill["inline_field_count"] == 5
    assert len(e["extensions"]) == 5  # inline part only in the event row
    view = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()
    assert view["mode"] == "SPILLED" and view["overflow_integrity_verified"] is True
    assert view["raw_hash_matches_event"] is True
    # Zero loss: inline ∪ overflow == every parsed field the adapter did not map.
    with TestSessionLocal() as db:
        full = db.execute(text("SELECT extensions FROM events WHERE event_id=:i"), {"i": e["event_id"]}).scalar_one()
    assert set(view["extensions"]) >= {f"custom_attr_{k:03d}" for k in range(40)}
    assert view["total_field_count"] == len(view["extensions"]) == spill["inline_field_count"] + spill["overflow_field_count"]
    assert all(view["extensions"][k] == v for k, v in parsed_fields(raw).items() if k in view["extensions"])
    assert full == view["inline"]
    # SHA-256 relationship untouched
    import hashlib

    assert e["raw_hash"] == hashlib.sha256(raw.encode()).hexdigest()


def test_lineage_field_accounting_counts_overflow_as_preserved(client, monkeypatch):
    spill_budget(monkeypatch)
    e = ingest(client, wide_json(2))
    lin = client.get(f"{API}/views/events/{e['event_id']}/lineage").json()
    acc = lin["field_accounting"]
    assert acc["unaccounted"] == [] and lin["nothing_silently_discarded"] is True
    assert acc["preserved_in_overflow"] == e["processing_metadata"]["extension_spill"]["overflow_field_count"]
    assert lin["evidence"]["extension_storage"]["mode"] == "SPILLED"
    row = client.get(f"{API}/views/events", params={"search": e["event_id"]}).json()["items"][0]
    assert row["extension_storage"] == "SPILLED"
    assert row["preserved_field_count"] == acc["preserved_count"]


def test_reprocess_keeps_overflow_consistent(client, monkeypatch):
    spill_budget(monkeypatch)
    e = ingest(client, wide_json(3))
    before = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()["extensions"]
    r = client.post(f"{API}/events/{e['event_id']}/reprocess")
    assert r.status_code == 200
    after = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()
    assert after["extensions"] == before and after["mode"] == "SPILLED"
    # A larger budget on reprocess brings everything back inline and removes the overflow row.
    spill_budget(monkeypatch, fields=1000)
    client.post(f"{API}/events/{e['event_id']}/reprocess")
    back = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()
    assert back["mode"] == "INLINE" and back["extensions"] == before


def test_overflow_stats_and_onboarding_evidence(client, monkeypatch):
    spill_budget(monkeypatch)
    events = [ingest(client, wide_json(i)) for i in range(3)]
    stats = client.get(f"{API}/integrity/overflow/stats").json()
    assert stats["events_spilled"] == 3 and stats["overflow_field_count"] > 0 and stats["evidence_signatures"] == 1
    assert "no storage saving is claimed" in stats["note"]
    evidence = client.get(f"{API}/integrity/overflow/evidence").json()["items"]
    assert len(evidence) == 1 and evidence[0]["occurrences"] == 3 and evidence[0]["onboarding_evidence"] is True
    assert set(evidence[0]["sample_event_ids"]) == {e["event_id"] for e in events}
    r = client.post(f"{API}/integrity/overflow/evidence/{evidence[0]['id']}/onboarding")
    assert r.status_code == 201, r.text
    session = client.get(f"{API}/onboarding/sessions/{r.json()['onboarding_session_id']}").json()
    assert session["sample_count"] == 3 and {s["source_event_id"] for s in session["samples"]} == {e["event_id"] for e in events}
    assert client.post(f"{API}/integrity/overflow/evidence/{evidence[0]['id']}/onboarding").status_code == 409
    trust = client.get(f"{API}/views/trust").json()
    assert trust["extension_overflow"]["events_spilled"] == 3


def test_below_threshold_is_not_onboarding_evidence(client, monkeypatch):
    spill_budget(monkeypatch)
    ingest(client, wide_json(1))
    item = client.get(f"{API}/integrity/overflow/evidence").json()["items"][0]
    assert item["onboarding_evidence"] is False
    assert client.post(f"{API}/integrity/overflow/evidence/{item['id']}/onboarding").status_code == 409


def test_byte_budget_triggers_spill(client, monkeypatch):
    spill_budget(monkeypatch, fields=10_000, size=300)
    e = ingest(client, json.dumps({"user": "x", "blob": "y" * 2000, "k": "v"}))
    assert e["processing_metadata"]["extension_spill"]["mode"] == "SPILLED"
    view = client.get(f"{API}/integrity/extensions/{e['event_id']}").json()
    assert view["extensions"]["blob"] == "y" * 2000
