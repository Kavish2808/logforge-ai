"""Merkle evidence chain: sealing, inclusion proofs, chain continuity, anchors,
and tamper detection at every layer."""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.evidence import merkle
from app.services import evidence_service, monitor_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest


def seal(client, force=True):
    r = client.post(f"{API}/integrity/seal", json={"force": force})
    assert r.status_code == 200, r.text
    return r.json()


def many(client, n, prefix="evt"):
    return [ingest(client, f'{{"user":"{prefix}{i}","action":"login","i":{i}}}') for i in range(n)]


def test_seal_creates_anchored_chained_batches(client, isolated_stores):
    events = many(client, 5)
    first = seal(client)["sealed_batches"]
    assert len(first) == 1 and first[0]["event_count"] == 5 and first[0]["prev_chain_hash"] == merkle.GENESIS
    events += many(client, 3, "second")
    second = seal(client)["sealed_batches"]
    assert second[0]["seq"] == 2 and second[0]["prev_chain_hash"] == first[0]["chain_hash"]
    assert seal(client)["sealed_batches"] == []  # nothing left to seal; idempotent
    assert isolated_stores["anchors"].get(1)["root_hash"] == first[0]["root_hash"]
    chain = client.get(f"{API}/integrity/verify").json()
    assert chain["valid"] and chain["batches_checked"] == 2 and chain["events_sealed"] == 8
    for e in events:
        v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
        assert v["status"] == "VERIFIED" and v["merkle"]["inclusion_valid"] and v["merkle"]["anchor_valid"]
        assert merkle.verify_inclusion(v["merkle"]["leaf_hash"], v["merkle"]["proof"], v["merkle"]["root_hash"])


def test_grace_period_leaves_recent_events_unsealed(client):
    many(client, 2)
    assert seal(client, force=False)["sealed_batches"] == []  # received < 60s ago
    with TestSessionLocal() as db:
        sealed = evidence_service.seal(db, now=datetime.now(tz=timezone.utc) + timedelta(minutes=5))
    assert len(sealed) == 1


def test_batches_respect_the_size_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "merkle_batch_max_events", 4)
    many(client, 10)
    batches = seal(client)["sealed_batches"]
    assert [b["event_count"] for b in batches] == [4, 4, 2]
    assert client.get(f"{API}/integrity/verify").json()["valid"]


def test_unsealed_event_verifies_hash_only(client):
    e = many(client, 1)[0]
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["status"] == "HASH_VALID_UNSEALED" and v["merkle"]["sealed"] is False and v["hash"]["valid"]


def test_modified_raw_event_is_detected(client):
    e = many(client, 3)[1]
    seal(client)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET raw_event = raw_event || ' ' WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["valid"] is False and v["hash"]["valid"] is False and v["status"] == "INTEGRITY_FAILURE"


def test_rewritten_hash_is_caught_by_the_merkle_leaf(client):
    """An attacker who edits raw_event AND rewrites raw_hash to match still breaks the sealed leaf."""
    e = many(client, 3)[0]
    seal(client)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET raw_event='forged', raw_hash=encode(sha256('forged'::bytea),'hex') "
                        "WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["hash"]["valid"] is True  # self-consistent forgery...
    assert v["merkle"]["leaf_matches_event"] is False and v["valid"] is False  # ...caught by the sealed leaf


def test_tampered_leaf_root_and_chain_are_detected(client):
    many(client, 4)
    seal(client)
    many(client, 2, "b")
    seal(client)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE evidence_batch_members SET leaf_hash=repeat('a',64) WHERE leaf_index=0 AND batch_seq=1"))
        db.commit()
    problems = {p["problem"] for p in client.get(f"{API}/integrity/verify").json()["problems"]}
    assert "LEAF_MODIFIED" in problems
    with TestSessionLocal() as db:
        db.execute(text("UPDATE evidence_batches SET root_hash=repeat('b',64) WHERE seq=2"))
        db.commit()
    problems = {p["problem"] for p in client.get(f"{API}/integrity/verify").json()["problems"]}
    assert {"ROOT_MISMATCH", "CHAIN_HASH_MISMATCH", "ANCHOR_MISMATCH"} <= problems


def test_deleted_batch_breaks_the_chain(client):
    for i in range(3):
        many(client, 2, f"r{i}")
        seal(client)
    with TestSessionLocal() as db:
        db.execute(text("DELETE FROM evidence_batch_members WHERE batch_seq=2"))
        db.execute(text("DELETE FROM evidence_batches WHERE seq=2"))
        db.commit()
    problems = {p["problem"] for p in client.get(f"{API}/integrity/verify").json()["problems"]}
    assert {"SEQUENCE_GAP", "BROKEN_CHAIN_LINK"} <= problems


def test_missing_anchor_is_detected_and_alerted(client, isolated_stores):
    many(client, 2)
    seal(client)
    path = isolated_stores["root"] / "anchors" / "000000000001.anchor.json"
    os.chmod(path, 0o644)
    path.unlink()
    assert "ANCHOR_MISSING" in {p["problem"] for p in client.get(f"{API}/integrity/verify").json()["problems"]}
    with TestSessionLocal() as db:
        monitor_service.sweep(db)
    alerts = client.get(f"{API}/alerts", params={"kind": "INTEGRITY_FAILURE"}).json()["items"]
    assert len(alerts) == 1 and alerts[0]["severity"] == "CRITICAL"


def test_deleted_event_keeps_its_sealed_evidence(client):
    e = many(client, 3)[2]
    seal(client)
    with TestSessionLocal() as db:
        db.execute(text("DELETE FROM events WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    chain = client.get(f"{API}/integrity/verify").json()
    assert chain["valid"] and chain["sealed_events_since_deleted"] == 1
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["event_present"] is False and v["merkle"]["inclusion_valid"] and v["status"] == "EVENT_DELETED"


def test_integrity_status_in_trust_view(client):
    many(client, 3)
    seal(client)
    many(client, 1, "late")
    t = client.get(f"{API}/views/trust").json()["integrity"]
    assert t["batches"] == 1 and t["events_sealed"] == 3 and t["events_unsealed"] == 1 and t["head"]["seq"] == 1
