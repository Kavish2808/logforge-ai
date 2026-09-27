"""Phase 7 evidence layer: extension spill, cold raw vault and the Merkle
evidence chain.

Ingestion integration is a single hook (`prepare_fields` + `persist_new` /
`persist_reprocessed`) called by ingestion_service inside the same database
transaction as the event row, so an event and its overflow / raw-storage
records commit together or not at all. The deterministic pipeline, the
event's raw payload, its SHA-256 and field accounting are untouched: spill
only moves *where* some extension values are stored, never whether.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.evidence import (
    RAW_FAILED,
    RAW_STORED,
    EventExtensionOverflow,
    EventRawStorage,
    EvidenceBatch,
    EvidenceBatchMember,
    OverflowSignature,
)
from app.evidence import merkle
from app.evidence.anchor import AnchorError, get_anchor_store
from app.evidence.canonical import canonical_sha256, sha256_bytes
from app.evidence.raw_vault import VaultError, get_vault
from app.evidence.spill import SPILLED, SpillResult, merge, split_extensions
from app.pipeline.hashing import sha256_hex

logger = logging.getLogger(__name__)

MAX_SIGNATURE_SAMPLES = 15
_SEAL_LOCK = 0x4C46_5345_414C  # "LFSEAL"


class EvidenceNotFound(Exception):
    pass


# --------------------------------------------------------------------------
# Ingestion hook
# --------------------------------------------------------------------------


def prepare_fields(fields: dict[str, Any]) -> SpillResult | None:
    """Apply the inline extension budget to pipeline output (in place).
    Returns the spill result when the event spills, else None."""
    settings = get_settings()
    extensions = fields.get("extensions") or {}
    result = split_extensions(extensions, max_bytes=settings.extension_inline_max_bytes,
                              max_fields=settings.extension_inline_max_fields)
    if result.mode != SPILLED:
        pm = dict(fields.get("processing_metadata") or {})
        pm.pop("extension_spill", None)  # a reprocessed event that no longer spills
        fields["processing_metadata"] = pm
        return None
    fields["extensions"] = result.inline
    fields["processing_metadata"] = {**(fields.get("processing_metadata") or {}), "extension_spill": result.metadata()}
    return result


def persist_new(db: Session, event: Event, spill: SpillResult | None) -> None:
    """Called after the event row is flushed, before commit."""
    if spill is not None:
        _write_overflow(db, event, spill)
    _archive_raw(db, event)


def persist_reprocessed(db: Session, event: Event, spill: SpillResult | None) -> None:
    """Reprocessing may change the extension set; the overflow row follows it."""
    db.execute(delete(EventExtensionOverflow).where(EventExtensionOverflow.event_id == event.event_id))
    if spill is not None:
        _write_overflow(db, event, spill)


def _write_overflow(db: Session, event: Event, spill: SpillResult) -> None:
    db.add(EventExtensionOverflow(
        event_id=event.event_id, raw_hash=event.raw_hash, adapter_id=event.adapter_id, payload=spill.overflow,
        key_order=spill.overflow_key_order, field_count=len(spill.overflow), byte_size=spill.overflow_bytes,
        payload_sha256=spill.overflow_sha256, key_signature=spill.key_signature,
    ))
    adapter = event.adapter_id or "unknown"
    now = datetime.now(tz=timezone.utc)
    stmt = pg_insert(OverflowSignature).values(
        adapter_id=adapter, key_signature=spill.key_signature, keys=sorted(spill.overflow_key_order),
        occurrences=1, sample_event_ids=[event.event_id], first_seen=now, last_seen=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[OverflowSignature.adapter_id, OverflowSignature.key_signature],
        set_={
            "occurrences": OverflowSignature.occurrences + 1,
            "last_seen": now,
            "sample_event_ids": text(
                f"CASE WHEN jsonb_array_length(overflow_signatures.sample_event_ids) < {MAX_SIGNATURE_SAMPLES} "
                "THEN overflow_signatures.sample_event_ids || excluded.sample_event_ids "
                "ELSE overflow_signatures.sample_event_ids END"
            ),
        },
    )
    db.execute(stmt)


def _archive_raw(db: Session, event: Event) -> EventRawStorage:
    """Write-through copy of the exact raw bytes to the cold vault. A vault
    failure never loses the event: the raw payload stays in PostgreSQL, the
    storage record says FAILED, and the sweep raises an alert and retries."""
    settings = get_settings()
    data = event.raw_event.encode("utf-8")
    row = db.get(EventRawStorage, event.event_id) or EventRawStorage(event_id=event.event_id, attempts=0)
    row.sha256 = event.raw_hash
    row.byte_size = len(data)
    row.encoding = "utf-8"
    row.attempts = (row.attempts or 0) + 1
    if not settings.raw_vault_enabled:
        row.tier, row.status, row.backend, row.object_key = "HOT_ONLY", RAW_FAILED, "disabled", None
        row.error = "raw vault disabled by configuration"
        db.add(row)
        return row
    try:
        vault = get_vault()
        obj = vault.put(data)
        if obj.sha256 != event.raw_hash:
            raise VaultError("vault digest differs from the event SHA-256")
        row.tier, row.status, row.backend, row.object_key = "HOT_AND_COLD", RAW_STORED, obj.backend, obj.key
        row.stored_at = datetime.now(tz=timezone.utc)
        row.error = None
    except Exception as exc:  # noqa: BLE001 — never lose the event over the cold copy
        logger.warning("Raw vault write failed for event %s (%s).", event.event_id, type(exc).__name__)
        row.tier, row.status, row.backend, row.object_key = "HOT_ONLY", RAW_FAILED, settings.raw_vault_backend, None
        row.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    db.add(row)
    return row


# --------------------------------------------------------------------------
# Extensions (inline + overflow)
# --------------------------------------------------------------------------


def overflow_for(db: Session, event_id: str) -> EventExtensionOverflow | None:
    return db.get(EventExtensionOverflow, event_id)


def overflow_map(db: Session, event_ids: list[str]) -> dict[str, EventExtensionOverflow]:
    if not event_ids:
        return {}
    rows = db.execute(select(EventExtensionOverflow).where(EventExtensionOverflow.event_id.in_(event_ids))).scalars()
    return {r.event_id: r for r in rows}


def full_extensions(event: Event, overflow: EventExtensionOverflow | None) -> dict[str, Any]:
    return merge(event.extensions or {}, overflow.payload if overflow else None, overflow.key_order if overflow else None)


def extension_view(db: Session, event_id: str) -> dict[str, Any]:
    event = db.get(Event, event_id)
    if event is None:
        raise EvidenceNotFound(f"Event '{event_id}' not found")
    overflow = overflow_for(db, event_id)
    spill = (event.processing_metadata or {}).get("extension_spill") or {}
    verified = None
    if overflow is not None:
        verified = canonical_sha256(overflow.payload) == overflow.payload_sha256 == spill.get("overflow_sha256")
    full = full_extensions(event, overflow)
    return {
        "event_id": event.event_id,
        "raw_hash": event.raw_hash,
        "mode": "SPILLED" if overflow is not None else "INLINE",
        "inline": event.extensions or {},
        "overflow": overflow.payload if overflow else {},
        "overflow_key_order": overflow.key_order if overflow else [],
        "extensions": full,
        "inline_field_count": len(event.extensions or {}),
        "overflow_field_count": overflow.field_count if overflow else 0,
        "overflow_bytes": overflow.byte_size if overflow else 0,
        "overflow_sha256": overflow.payload_sha256 if overflow else None,
        "overflow_integrity_verified": verified,
        "raw_hash_matches_event": overflow is None or overflow.raw_hash == event.raw_hash,
        "spill_metadata": spill or None,
        "total_field_count": len(full),
    }


def overflow_stats(db: Session) -> dict[str, Any]:
    spilled, fields, size = db.execute(select(
        func.count(), func.coalesce(func.sum(EventExtensionOverflow.field_count), 0),
        func.coalesce(func.sum(EventExtensionOverflow.byte_size), 0))).one()
    total_events = db.execute(select(func.count()).select_from(Event)).scalar_one()
    by_adapter = {
        (a or "unknown"): {"events": n, "fields": int(f), "bytes": int(b)}
        for a, n, f, b in db.execute(
            select(EventExtensionOverflow.adapter_id, func.count(), func.sum(EventExtensionOverflow.field_count),
                   func.sum(EventExtensionOverflow.byte_size))
            .group_by(EventExtensionOverflow.adapter_id).order_by(func.count().desc()).limit(50)).all()
    }
    settings = get_settings()
    return {
        "events_total": total_events,
        "events_spilled": spilled,
        "events_inline": total_events - spilled,
        "overflow_field_count": int(fields),
        "overflow_bytes": int(size),
        "by_adapter": by_adapter,
        "budget": {"max_bytes": settings.extension_inline_max_bytes, "max_fields": settings.extension_inline_max_fields},
        "evidence_signatures": db.execute(select(func.count()).select_from(OverflowSignature).where(
            OverflowSignature.occurrences >= settings.overflow_evidence_min_occurrences)).scalar_one(),
        "note": "Sizes are canonical-JSON byte counts of the overflow payloads; no storage saving is claimed.",
    }


def overflow_evidence(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    threshold = get_settings().overflow_evidence_min_occurrences
    rows = db.execute(select(OverflowSignature).order_by(OverflowSignature.occurrences.desc()).limit(limit)).scalars()
    return [signature_dict(r, threshold) for r in rows]


def signature_dict(r: OverflowSignature, threshold: int | None = None) -> dict[str, Any]:
    threshold = threshold or get_settings().overflow_evidence_min_occurrences
    return {
        "id": r.id, "adapter_id": r.adapter_id, "key_signature": r.key_signature, "keys": r.keys,
        "key_count": len(r.keys), "occurrences": r.occurrences, "sample_event_ids": r.sample_event_ids,
        "first_seen": r.first_seen, "last_seen": r.last_seen, "onboarding_session_id": r.onboarding_session_id,
        "onboarding_evidence": r.occurrences >= threshold,
        "recommended_action": (
            "Repeated unmapped structure: start an onboarding/adapter review with these sample events"
            if r.occurrences >= threshold else "Observed; below the onboarding-evidence threshold"),
    }


# --------------------------------------------------------------------------
# Raw vault
# --------------------------------------------------------------------------


def raw_status(db: Session, event_id: str) -> dict[str, Any]:
    event = db.get(Event, event_id)
    if event is None:
        raise EvidenceNotFound(f"Event '{event_id}' not found")
    row = db.get(EventRawStorage, event_id)
    return {"event_id": event_id, "raw_hash": event.raw_hash, "storage": storage_dict(row)}


def storage_dict(row: EventRawStorage | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"tier": row.tier, "status": row.status, "backend": row.backend, "object_key": row.object_key,
            "sha256": row.sha256, "byte_size": row.byte_size, "encoding": row.encoding, "error": row.error,
            "attempts": row.attempts, "stored_at": row.stored_at, "verified_at": row.verified_at}


def recover_raw(db: Session, event_id: str, *, record_verification: bool = False) -> dict[str, Any]:
    """Read the raw payload back from the cold vault and prove it is the
    exact byte sequence the event was ingested with."""
    event = db.get(Event, event_id)
    if event is None:
        raise EvidenceNotFound(f"Event '{event_id}' not found")
    row = db.get(EventRawStorage, event_id)
    if row is None or row.status != RAW_STORED or not row.object_key:
        return {"event_id": event_id, "recovered": False, "reason": "no cold copy stored for this event",
                "storage": storage_dict(row)}
    try:
        data = get_vault(row.backend).get(row.object_key)  # the vault the object was written to
    except VaultError as exc:
        return {"event_id": event_id, "recovered": False, "reason": str(exc), "storage": storage_dict(row)}
    digest = sha256_bytes(data)
    hot = event.raw_event.encode("utf-8")
    result = {
        "event_id": event_id,
        "recovered": True,
        "object_key": row.object_key,
        "byte_size": len(data),
        "sha256": digest,
        "matches_event_hash": digest == event.raw_hash,
        "matches_hot_copy": data == hot,
        "hot_copy_hash_valid": sha256_hex(event.raw_event) == event.raw_hash,
        "raw_event": data.decode("utf-8"),
    }
    if record_verification and result["matches_event_hash"]:
        row.verified_at = datetime.now(tz=timezone.utc)
        db.commit()
    return result


def backfill_vault(db: Session, *, limit: int = 500) -> dict[str, int]:
    """Archive events that have no cold copy yet (pre-Phase-7 events, or the
    raw-only fallback path) and retry FAILED writes."""
    if not get_settings().raw_vault_enabled:
        return {"archived": 0, "failed": 0}
    missing = db.execute(
        select(Event).outerjoin(EventRawStorage, EventRawStorage.event_id == Event.event_id)
        .where((EventRawStorage.event_id.is_(None)) | (EventRawStorage.status == RAW_FAILED))
        .order_by(Event.received_at).limit(limit)
    ).scalars().all()
    archived = failed = 0
    for event in missing:
        row = _archive_raw(db, event)
        archived += row.status == RAW_STORED
        failed += row.status != RAW_STORED
    db.commit()
    return {"archived": archived, "failed": failed}


def vault_stats(db: Session) -> dict[str, Any]:
    by = {f"{t}:{s}": n for t, s, n in db.execute(
        select(EventRawStorage.tier, EventRawStorage.status, func.count())
        .group_by(EventRawStorage.tier, EventRawStorage.status)).all()}
    stored, stored_bytes = db.execute(select(func.count(), func.coalesce(func.sum(EventRawStorage.byte_size), 0))
                                      .where(EventRawStorage.status == RAW_STORED)).one()
    total = db.execute(select(func.count()).select_from(Event)).scalar_one()
    tracked = db.execute(select(func.count()).select_from(EventRawStorage)).scalar_one()
    failed = db.execute(select(func.count()).select_from(EventRawStorage).where(EventRawStorage.status == RAW_FAILED)).scalar_one()
    settings = get_settings()
    try:
        backend = get_vault().describe()
    except VaultError as exc:
        backend = {"backend": settings.raw_vault_backend, "error": str(exc)}
    return {
        "enabled": settings.raw_vault_enabled,
        "backend": backend,
        "events_total": total,
        "hot": total,  # every event keeps its raw payload in PostgreSQL (hot); see known limitations
        "cold_stored": stored,
        "cold_bytes": int(stored_bytes),
        "cold_failed": failed,
        "not_yet_archived": max(total - tracked, 0),
        "by_tier_status": by,
    }


# --------------------------------------------------------------------------
# Merkle evidence chain
# --------------------------------------------------------------------------


def seal(db: Session, *, force: bool = False, now: datetime | None = None, max_batches: int = 10) -> list[EvidenceBatch]:
    """Seal unsealed events (received before the grace cutoff; all of them
    when force=True) into Merkle batches of at most MERKLE_BATCH_MAX_EVENTS,
    each chained to the previous batch, then anchor each batch root to the
    immutable anchor store."""
    settings = get_settings()
    now = now or datetime.now(tz=timezone.utc)
    cutoff = now if force else now - timedelta(seconds=settings.merkle_seal_grace_seconds)
    sealed: list[EvidenceBatch] = []
    anchor_pending(db)
    for _ in range(max_batches):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SEAL_LOCK})
        rows = db.execute(
            select(Event.event_id, Event.raw_hash, Event.received_at)
            .outerjoin(EvidenceBatchMember, EvidenceBatchMember.event_id == Event.event_id)
            .where(EvidenceBatchMember.event_id.is_(None), Event.received_at <= cutoff)
            .order_by(Event.received_at, Event.event_id)
            .limit(settings.merkle_batch_max_events)
        ).all()
        if not rows:
            db.commit()
            break
        last = db.execute(select(EvidenceBatch).order_by(EvidenceBatch.seq.desc()).limit(1)).scalars().first()
        seq = (last.seq + 1) if last else 1
        prev = last.chain_hash if last else merkle.GENESIS
        leaves = [merkle.leaf_hash(eid, h) for eid, h, _ in rows]
        root = merkle.merkle_root(leaves)
        batch = EvidenceBatch(
            seq=seq, batch_id=generate_event_id(), start_time=rows[0][2], end_time=rows[-1][2],
            event_count=len(rows), root_hash=root, prev_chain_hash=prev,
            chain_hash=merkle.chain_hash(seq, prev, root, len(rows)), leaf_algorithm=merkle.LEAF_ALGORITHM,
            anchor_backend=get_anchor_store().backend,
        )
        db.add(batch)
        db.flush()
        db.execute(pg_insert(EvidenceBatchMember), [
            {"event_id": eid, "batch_seq": seq, "leaf_index": i, "raw_hash": h, "leaf_hash": leaves[i]}
            for i, (eid, h, _) in enumerate(rows)
        ])
        db.commit()
        _anchor(db, batch)
        sealed.append(batch)
    return sealed


def _anchor(db: Session, batch: EvidenceBatch) -> bool:
    """Anchor a committed batch. A failure leaves the batch unanchored
    (reported as ANCHOR_MISSING by verification) and is retried later."""
    try:
        batch.anchor_ref = get_anchor_store(batch.anchor_backend).append(anchor_record(batch))
    except AnchorError as exc:
        logger.error("Anchoring evidence batch %s failed: %s", batch.seq, exc)
        return False
    batch.anchored_at = datetime.now(tz=timezone.utc)
    db.commit()
    return True


def anchor_pending(db: Session) -> int:
    pending = db.execute(select(EvidenceBatch).where(EvidenceBatch.anchored_at.is_(None))
                         .order_by(EvidenceBatch.seq)).scalars().all()
    return sum(_anchor(db, b) for b in pending)


def anchor_record(batch: EvidenceBatch) -> dict[str, Any]:
    return {"seq": batch.seq, "batch_id": batch.batch_id, "root_hash": batch.root_hash,
            "prev_chain_hash": batch.prev_chain_hash, "chain_hash": batch.chain_hash,
            "event_count": batch.event_count, "start_time": _iso(batch.start_time), "end_time": _iso(batch.end_time)}


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def _leaves(db: Session, seq: int) -> list[EvidenceBatchMember]:
    return list(db.execute(select(EvidenceBatchMember).where(EvidenceBatchMember.batch_seq == seq)
                           .order_by(EvidenceBatchMember.leaf_index)).scalars().all())


def verify_chain(db: Session, *, recent: int | None = None) -> dict[str, Any]:
    """Recompute every batch root from its stored leaves, check each leaf is
    derived from its recorded (event_id, raw_hash), check chain continuity,
    and compare every batch with its immutable anchor. With `recent`, only the
    newest batches are checked (linked to their predecessor's chain hash)."""
    store = get_anchor_store()
    problems: list[dict[str, Any]] = []
    prev = merkle.GENESIS
    expected_seq = 1
    stmt = select(EvidenceBatch).order_by(EvidenceBatch.seq)
    if recent:
        head = db.execute(select(func.max(EvidenceBatch.seq))).scalar() or 0
        start = max(1, head - recent + 1)
        stmt = stmt.where(EvidenceBatch.seq >= start)
        if start > 1:
            before = db.get(EvidenceBatch, start - 1)
            prev = before.chain_hash if before else merkle.GENESIS
            expected_seq = start
    batches = db.execute(stmt).scalars().all()
    for b in batches:
        if b.seq != expected_seq:
            problems.append({"seq": b.seq, "problem": "SEQUENCE_GAP", "expected_seq": expected_seq})
        if b.prev_chain_hash != prev:
            problems.append({"seq": b.seq, "problem": "BROKEN_CHAIN_LINK"})
        members = _leaves(db, b.seq)
        bad_leaves = [m.leaf_index for m in members if merkle.leaf_hash(m.event_id, m.raw_hash) != m.leaf_hash]
        if bad_leaves:
            problems.append({"seq": b.seq, "problem": "LEAF_MODIFIED", "leaf_indexes": bad_leaves[:20]})
        if len(members) != b.event_count or [m.leaf_index for m in members] != list(range(len(members))):
            problems.append({"seq": b.seq, "problem": "MEMBERSHIP_CHANGED",
                             "expected": b.event_count, "found": len(members)})
        elif merkle.merkle_root([m.leaf_hash for m in members]) != b.root_hash:
            problems.append({"seq": b.seq, "problem": "ROOT_MISMATCH"})
        if merkle.chain_hash(b.seq, b.prev_chain_hash, b.root_hash, b.event_count) != b.chain_hash:
            problems.append({"seq": b.seq, "problem": "CHAIN_HASH_MISMATCH"})
        try:
            batch_store = get_anchor_store(b.anchor_backend)  # the provider this batch was anchored to
        except AnchorError as exc:
            problems.append({"seq": b.seq, "problem": "ANCHOR_BACKEND_UNAVAILABLE", "detail": str(exc)})
            prev = b.chain_hash
            expected_seq = b.seq + 1
            continue
        try:
            anchored = batch_store.get(b.seq)
        except AnchorError as exc:
            anchored = None
            problems.append({"seq": b.seq, "problem": "ANCHOR_UNREADABLE", "detail": str(exc)})
        else:
            if anchored is None:
                problems.append({"seq": b.seq, "problem": "ANCHOR_MISSING"})
            elif anchored != anchor_record(b):
                problems.append({"seq": b.seq, "problem": "ANCHOR_MISMATCH",
                                 "detail": "database batch differs from its immutable anchor"})
        prev = b.chain_hash
        expected_seq = b.seq + 1
    deleted = None if recent else db.execute(
        select(func.count()).select_from(EvidenceBatchMember)
        .outerjoin(Event, Event.event_id == EvidenceBatchMember.event_id).where(Event.event_id.is_(None))
    ).scalar_one()
    return {
        "valid": not problems,
        "batches_checked": len(batches),
        "events_sealed": sum(b.event_count for b in batches),
        "head": None if not batches else {"seq": batches[-1].seq, "chain_hash": batches[-1].chain_hash,
                                          "root_hash": batches[-1].root_hash},
        "problems": problems[:100],
        # Sealed events whose rows were later deleted (e.g. a Demo Mode reset).
        # The sealed evidence stays verifiable; the events themselves are gone.
        "sealed_events_since_deleted": deleted,
        "anchor_store": store.describe(),
        "scope": f"newest {recent} batches" if recent else "full chain",
        "verified_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def verify_event(db: Session, event_id: str) -> dict[str, Any]:
    """Three independent checks: the stored SHA-256 matches the raw payload;
    the event is included in its sealed batch's Merkle root (with proof); and
    that batch's anchor matches. Also checks the cold copy if one exists."""
    event = db.get(Event, event_id)
    member = db.get(EvidenceBatchMember, event_id)
    if event is None and member is None:
        raise EvidenceNotFound(f"Event '{event_id}' not found")
    out: dict[str, Any] = {"event_id": event_id, "event_present": event is not None}
    if event is not None:
        recomputed = sha256_hex(event.raw_event)
        out["hash"] = {"stored": event.raw_hash, "recomputed": recomputed, "valid": recomputed == event.raw_hash}
        row = db.get(EventRawStorage, event_id)
        if row is not None and row.status == RAW_STORED and row.object_key:
            try:
                ok = get_vault(row.backend).verify(row.object_key, event.raw_hash)
                out["cold_copy"] = {"object_key": row.object_key, "valid": ok}
            except VaultError as exc:  # recorded backend not available in this build: cannot verify, not "valid"
                out["cold_copy"] = {"object_key": row.object_key, "valid": None, "status": "BACKEND_UNAVAILABLE",
                                    "detail": str(exc)}
        else:
            out["cold_copy"] = {"object_key": None, "valid": None, "status": row.status if row else "NOT_ARCHIVED"}
    if member is None:
        out["merkle"] = {"sealed": False, "valid": None}
        out["valid"] = bool(out.get("hash", {}).get("valid")) and out["cold_copy"].get("valid") is not False
        out["status"] = "HASH_VALID_UNSEALED" if out["valid"] else "INTEGRITY_FAILURE"
        return out
    batch = db.get(EvidenceBatch, member.batch_seq)
    members = _leaves(db, member.batch_seq)
    leaves = [m.leaf_hash for m in members]
    proof = merkle.inclusion_proof(leaves, member.leaf_index)
    current_leaf = merkle.leaf_hash(event_id, event.raw_hash) if event is not None else None
    leaf_ok = current_leaf == member.leaf_hash if event is not None else None
    included = merkle.verify_inclusion(member.leaf_hash, proof, batch.root_hash)
    anchor_error = None
    try:
        anchored = get_anchor_store(batch.anchor_backend).get(batch.seq)
        anchor_ok = anchored == anchor_record(batch)
    except AnchorError as exc:
        anchor_ok = False
        anchor_error = str(exc)
    out["merkle"] = {
        "sealed": True, "batch_id": batch.batch_id, "batch_seq": batch.seq, "leaf_index": member.leaf_index,
        "leaf_hash": member.leaf_hash, "current_leaf_hash": current_leaf, "leaf_matches_event": leaf_ok,
        "root_hash": batch.root_hash, "proof": proof, "inclusion_valid": included, "anchor_valid": anchor_ok,
        "anchor_backend": batch.anchor_backend, "anchor_error": anchor_error, "chain_hash": batch.chain_hash,
    }
    hash_ok = out.get("hash", {}).get("valid", True)
    cold_ok = (out.get("cold_copy") or {}).get("valid") is not False
    out["valid"] = bool(hash_ok and cold_ok and included and anchor_ok and leaf_ok is not False)
    if event is None:
        # The sealed evidence itself still verifies; the event row is gone.
        out["status"] = "EVENT_DELETED" if out["valid"] else "INTEGRITY_FAILURE"
    else:
        out["status"] = "VERIFIED" if out["valid"] else "INTEGRITY_FAILURE"
    return out


def integrity_stats(db: Session) -> dict[str, Any]:
    batches, sealed = db.execute(select(func.count(), func.coalesce(func.sum(EvidenceBatch.event_count), 0))).one()
    head = db.execute(select(EvidenceBatch).order_by(EvidenceBatch.seq.desc()).limit(1)).scalars().first()
    unsealed = db.execute(
        select(func.count()).select_from(Event)
        .outerjoin(EvidenceBatchMember, EvidenceBatchMember.event_id == Event.event_id)
        .where(EvidenceBatchMember.event_id.is_(None))
    ).scalar_one()
    return {
        "batches": batches,
        "events_sealed": int(sealed),
        "events_unsealed": unsealed,
        "head": None if head is None else {"seq": head.seq, "batch_id": head.batch_id, "root_hash": head.root_hash,
                                           "chain_hash": head.chain_hash, "event_count": head.event_count,
                                           "end_time": head.end_time, "anchored_at": head.anchored_at},
    }


def list_batches(db: Session, *, limit: int, before_seq: int | None = None) -> list[dict[str, Any]]:
    stmt = select(EvidenceBatch)
    if before_seq:
        stmt = stmt.where(EvidenceBatch.seq < before_seq)
    rows = db.execute(stmt.order_by(EvidenceBatch.seq.desc()).limit(limit)).scalars().all()
    return [{**anchor_record(b), "leaf_algorithm": b.leaf_algorithm, "anchor_backend": b.anchor_backend,
             "anchor_ref": b.anchor_ref, "anchored_at": b.anchored_at, "created_at": b.created_at} for b in rows]


def membership_map(db: Session, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not event_ids:
        return {}
    rows = db.execute(
        select(EvidenceBatchMember, EvidenceBatch)
        .join(EvidenceBatch, EvidenceBatch.seq == EvidenceBatchMember.batch_seq)
        .where(EvidenceBatchMember.event_id.in_(event_ids))
    ).all()
    return {m.event_id: {"batch_id": b.batch_id, "batch_seq": b.seq, "leaf_index": m.leaf_index,
                         "leaf_hash": m.leaf_hash, "root_hash": b.root_hash, "chain_hash": b.chain_hash}
            for m, b in rows}


def raw_storage_map(db: Session, event_ids: list[str]) -> dict[str, EventRawStorage]:
    if not event_ids:
        return {}
    rows = db.execute(select(EventRawStorage).where(EventRawStorage.event_id.in_(event_ids))).scalars()
    return {r.event_id: r for r in rows}