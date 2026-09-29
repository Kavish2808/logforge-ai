"""Global Merkle verification re-hashes every stored raw event instead of trusting `events.raw_hash`
(E2E defect D2: a tampered `raw_event` with an untouched `raw_hash` used to verify as valid)."""
from sqlalchemy import text

from app.pipeline.hashing import sha256_hex
from app.services import evidence_service, monitor_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest


def seal(client):
    r = client.post(f"{API}/integrity/seal", json={"force": True})
    assert r.status_code == 200, r.text
    return r.json()["sealed_batches"]


def many(client, n, prefix="evt"):
    return [ingest(client, f'{{"user":"{prefix}{i}","action":"login","i":{i}}}') for i in range(n)]


def verify(client):
    r = client.get(f"{API}/integrity/verify")
    assert r.status_code == 200, r.text
    return r.json()


def problems(chain):
    return {p["problem"]: p for p in chain["problems"]}


def sql(statement, **params):
    with TestSessionLocal() as db:
        db.execute(text(statement), params)
        db.commit()


def test_a_untampered_chain_is_valid_and_every_event_is_rehashed(client):
    many(client, 4)
    seal(client)
    many(client, 2, "unsealed")
    chain = verify(client)
    assert chain["valid"] is True and chain["problems"] == []
    assert chain["events_rehashed"] == 6  # 4 sealed + 2 unsealed, all re-hashed


def test_database_rehash_matches_the_ingest_hash_function(client):
    raw = '{"msg":"Ünïcödé 日本語 🚀 \\t tab","user":"zoë"}'
    e = ingest(client, raw)
    seal(client)
    assert e["raw_hash"] == sha256_hex(raw)
    assert verify(client)["valid"] is True  # PostgreSQL SHA-256 over UTF-8 == app hashing (non-ASCII too)


def test_b_tampered_raw_event_alone_fails_global_verification(client):
    events = many(client, 3)
    batch = seal(client)[0]
    victim = events[1]["event_id"]
    sql("UPDATE events SET raw_event = replace(raw_event, 'login', 'logout') WHERE event_id=:i", i=victim)
    chain = verify(client)
    assert chain["valid"] is False
    p = problems(chain)["RAW_HASH_MISMATCH"]
    assert p["event_ids"] == [victim] and p["count"] == 1 and p["batch_seqs"] == [batch["seq"]] and p["sealed"] is True
    assert "EVENT_HASH_NOT_SEALED_LEAF" not in problems(chain)  # raw_hash itself was left untouched


def test_c_tampered_raw_hash_alone_fails_global_verification(client):
    victim = many(client, 3)[0]["event_id"]
    seal(client)
    sql("UPDATE events SET raw_hash = repeat('0', 64) WHERE event_id=:i", i=victim)
    p = problems(verify(client))
    assert p["RAW_HASH_MISMATCH"]["event_ids"] == [victim]
    assert p["EVENT_HASH_NOT_SEALED_LEAF"]["event_ids"] == [victim]


def test_d_consistent_forgery_of_raw_event_and_raw_hash_is_caught_by_the_sealed_leaf(client):
    victim = many(client, 3)[2]["event_id"]
    seal(client)
    sql("UPDATE events SET raw_event='forged', raw_hash=encode(sha256('forged'::bytea),'hex') WHERE event_id=:i",
        i=victim)
    chain = verify(client)
    p = problems(chain)
    assert chain["valid"] is False and "RAW_HASH_MISMATCH" not in p  # self-consistent...
    assert p["EVENT_HASH_NOT_SEALED_LEAF"]["event_ids"] == [victim]  # ...but not what was sealed
    # Forging the sealed member row too (raw_hash + leaf) breaks the leaf derivation / root instead.
    sql("UPDATE evidence_batch_members SET raw_hash=encode(sha256('forged'::bytea),'hex') WHERE event_id=:i", i=victim)
    assert "LEAF_MODIFIED" in problems(verify(client))


def test_e_multiple_batches_verify_and_tamper_is_located_in_its_batch(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "merkle_batch_max_events", 3)
    events = many(client, 8)
    batches = seal(client)
    assert [b["event_count"] for b in batches] == [3, 3, 2]
    assert verify(client)["valid"] is True
    victim = events[4]["event_id"]  # sealed in the 2nd batch
    with TestSessionLocal() as db:
        seq = db.execute(text("SELECT batch_seq FROM evidence_batch_members WHERE event_id=:i"), {"i": victim}).scalar()
    sql("UPDATE events SET raw_event = raw_event || ' ' WHERE event_id=:i", i=victim)
    assert problems(verify(client))["RAW_HASH_MISMATCH"]["batch_seqs"] == [seq]


def test_f_reprocessed_events_with_revisions_keep_the_chain_valid(client):
    events = many(client, 3)
    seal(client)
    for e in events:
        r = client.post(f"{API}/events/{e['event_id']}/reprocess")
        assert r.status_code == 200, r.text
        assert client.get(f"{API}/revisions/{e['event_id']}").json()["count"] == 2
    chain = verify(client)
    assert chain["valid"] is True and chain["events_rehashed"] == 3


def test_g_intact_vault_copy_does_not_mask_a_tampered_database_row(client):
    e = many(client, 2)[0]
    seal(client)
    sql("UPDATE events SET raw_event = raw_event || 'x' WHERE event_id=:i", i=e["event_id"])
    assert problems(verify(client))["RAW_HASH_MISMATCH"]["event_ids"] == [e["event_id"]]
    rec = client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()
    assert rec["matches_event_hash"] is True and rec["matches_hot_copy"] is False  # evidence still recoverable


def test_unsealed_tampered_event_is_detected_in_full_scope(client):
    e = many(client, 2)[1]  # never sealed
    sql("UPDATE events SET raw_event = raw_event || 'x' WHERE event_id=:i", i=e["event_id"])
    p = problems(verify(client))["RAW_HASH_MISMATCH"]
    assert p["sealed"] is False and p["event_ids"] == [e["event_id"]]


def test_recent_scope_rehashes_the_checked_batches_and_the_sweep_alerts(client):
    victim = many(client, 2)[0]["event_id"]
    seal(client)
    sql("UPDATE events SET raw_event = raw_event || 'x' WHERE event_id=:i", i=victim)
    with TestSessionLocal() as db:
        recent = evidence_service.verify_chain(db, recent=20)
        assert recent["valid"] is False and recent["events_rehashed"] == 2
        monitor_service.sweep(db)
    alerts = client.get(f"{API}/alerts", params={"kind": "INTEGRITY_FAILURE"}).json()["items"]
    assert len(alerts) == 1 and "RAW_HASH_MISMATCH" in alerts[0]["message"]


def test_verification_never_repairs_the_stored_hash(client):
    victim = many(client, 1)[0]["event_id"]
    seal(client)
    sql("UPDATE events SET raw_event = 'tampered' WHERE event_id=:i", i=victim)
    for _ in range(2):
        assert verify(client)["valid"] is False
    with TestSessionLocal() as db:
        stored = db.execute(text("SELECT raw_hash FROM events WHERE event_id=:i"), {"i": victim}).scalar()
    assert stored != sha256_hex("tampered")
