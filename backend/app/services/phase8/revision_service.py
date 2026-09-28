"""Phase 8 Step 7: append-only event revisions.

An event row always holds its CURRENT processing result (Phase 0 reprocess
overwrites it in place). Revisions keep every earlier result, through the
Phase 7 persist hook (H3) — the reprocess engine itself is unchanged:

- before the first reprocess of an event, its stored state is captured as
  revision 1 (ORIGINAL);
- the reprocessed state is appended right before that transaction commits
  (the hook fires before the new fields are assigned), as REPLAY when a
  replay job drives it (job id, actor, reason from `context`) or REPROCESS
  for the manual endpoint;
- revisions are never updated except the `is_current` pointer, never
  deleted by the application, and at most one per (event, replay job).

Each revision stores a canonical snapshot of every processing column (no raw
bytes — those never change and are referenced by raw_hash) plus its
SHA-256, so a stored revision can be re-verified at any time.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.evidence import EventExtensionOverflow
from app.db.models.phase8 import EventRevision
from app.evidence.canonical import canonical_sha256

logger = logging.getLogger(__name__)

HOOK_NAME = "event_revisions"
ORIGINAL, REPLAY, REPROCESS = "ORIGINAL", "REPLAY", "REPROCESS"
_CONTEXT = "phase8_revision_context"
_PENDING = "phase8_revision_pending"
_LISTENING = "phase8_revision_listening"
SNAPSHOT_COLUMNS = (
    "status", "format_detected", "vendor", "product", "product_version", "adapter_id", "adapter_version",
    "ocsf_class_uid", "ocsf_class_name", "ocsf_category_uid", "ocsf_category_name", "event_type", "event_action",
    "severity", "severity_id", "event_timestamp", "processed_at", "network", "user", "process", "extensions",
    "normalized_event", "processing_metadata", "structural_fingerprint", "warnings", "error_message",
)


class RevisionNotFound(Exception):
    pass


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def snapshot(db: Session, e: Event, *, with_overflow: bool = True) -> dict[str, Any]:
    snap = {c: _jsonable(getattr(e, c)) for c in SNAPSHOT_COLUMNS}
    spill = (e.processing_metadata or {}).get("extension_spill") or {}
    if with_overflow and spill.get("mode") == "SPILLED":
        row = db.get(EventExtensionOverflow, e.event_id)
        snap["extension_overflow"] = ({"payload": row.payload, "key_order": row.key_order,
                                       "sha256": row.payload_sha256} if row is not None
                                      else {"sha256": spill.get("overflow_sha256"), "payload": None})
    elif spill.get("mode") == "SPILLED":
        # Phase 7 reprocess replaces the overflow row before hooks run; its digest stays referenced.
        snap["extension_overflow"] = {"sha256": spill.get("overflow_sha256"), "payload": None,
                                      "note": "overflow values replaced by reprocess; digest retained"}
    return snap


@contextmanager
def context(db: Session, *, trigger: str, actor: str, reason: str | None, replay_job_id: str | None):
    db.info[_CONTEXT] = {"trigger": trigger, "actor": actor, "reason": reason, "replay_job_id": replay_job_id}
    try:
        yield
    finally:
        db.info.pop(_CONTEXT, None)


def current(db: Session, event_id: str) -> EventRevision | None:
    return db.execute(select(EventRevision).where(EventRevision.event_id == event_id, EventRevision.is_current)
                      ).scalars().first()


def append(db: Session, e: Event, snap: dict[str, Any], *, trigger: str, actor: str, reason: str | None,
           replay_job_id: str | None) -> EventRevision:
    parent = current(db, e.event_id)
    number = (db.execute(select(func.max(EventRevision.revision_no)).where(EventRevision.event_id == e.event_id))
              .scalar() or 0) + 1
    if parent is not None:
        db.execute(update(EventRevision).where(EventRevision.id == parent.id).values(is_current=False))
    rev = EventRevision(
        id=generate_event_id(), event_id=e.event_id, revision_no=number,
        parent_revision_id=parent.id if parent else None, is_current=True, trigger=trigger, reason=reason,
        adapter_id=snap.get("adapter_id"), adapter_version=snap.get("adapter_version"), replay_job_id=replay_job_id,
        snapshot=snap, snapshot_sha256=canonical_sha256(snap), raw_hash=e.raw_hash, actor=actor[:128],
    )
    db.add(rev)
    db.flush()
    return rev


def ensure_original(db: Session, e: Event, *, with_overflow: bool = True) -> EventRevision | None:
    if db.execute(select(EventRevision.id).where(EventRevision.event_id == e.event_id).limit(1)).first():
        return None
    return append(db, e, snapshot(db, e, with_overflow=with_overflow), trigger=ORIGINAL, actor="system",
                  reason="stored state before the first reprocess/replay", replay_job_id=None)


# --------------------------------------------------------------------------
# H3 persist hook
# --------------------------------------------------------------------------


def persist_hook(db: Session, event: Event, reprocessed: bool) -> None:
    if not reprocessed:
        return  # a never-reprocessed event has exactly one state: the event row
    ensure_original(db, event, with_overflow=False)
    ctx = dict(db.info.get(_CONTEXT) or {"trigger": REPROCESS, "actor": "reprocess-api",
                                         "reason": "manual reprocess (see audit EVENT_REPROCESS)",
                                         "replay_job_id": None})
    db.info.setdefault(_PENDING, {})[event.event_id] = (event, ctx)
    if not db.info.get(_LISTENING):
        sa_event.listen(db, "before_commit", _flush_pending)
        sa_event.listen(db, "after_soft_rollback", _drop_pending)
        db.info[_LISTENING] = True


def _flush_pending(db: Session) -> None:
    if db.in_nested_transaction():
        return
    pending = db.info.pop(_PENDING, None)
    if not pending:
        return
    db.flush()
    for event, ctx in pending.values():
        savepoint = db.begin_nested()
        try:
            append(db, event, snapshot(db, event), trigger=ctx["trigger"], actor=ctx["actor"], reason=ctx["reason"],
                   replay_job_id=ctx["replay_job_id"])
            savepoint.commit()
        except Exception:  # noqa: BLE001 — the reprocessed event must commit regardless; replay verifies
            savepoint.rollback()
            logger.exception("Recording the revision of event %s failed.", event.event_id)


def _drop_pending(db: Session, previous_transaction) -> None:
    if previous_transaction.parent is None and not previous_transaction.nested:
        db.info.pop(_PENDING, None)


# --------------------------------------------------------------------------
# Read API
# --------------------------------------------------------------------------


def revision_dict(r: EventRevision, *, verify_raw_hash: str | None = None) -> dict[str, Any]:
    snap = r.snapshot or {}
    return {"id": r.id, "event_id": r.event_id, "revision_no": r.revision_no, "parent_revision_id": r.parent_revision_id,
            "is_current": r.is_current, "trigger": r.trigger, "reason": r.reason, "actor": r.actor,
            "adapter_id": r.adapter_id, "adapter_version": r.adapter_version, "replay_job_id": r.replay_job_id,
            "resulting_status": snap.get("status"), "processed_at": snap.get("processed_at"),
            "raw_hash": r.raw_hash, "snapshot_sha256": r.snapshot_sha256,
            "snapshot_verified": canonical_sha256(snap) == r.snapshot_sha256,
            "raw_hash_matches_event": None if verify_raw_hash is None else r.raw_hash == verify_raw_hash,
            "created_at": r.created_at, "snapshot": snap}


def history(db: Session, event_id: str) -> dict[str, Any]:
    e = db.get(Event, event_id)
    rows = db.execute(select(EventRevision).where(EventRevision.event_id == event_id)
                      .order_by(EventRevision.revision_no)).scalars().all()
    if e is None and not rows:
        raise RevisionNotFound(f"Event '{event_id}' not found")
    items = [revision_dict(r, verify_raw_hash=e.raw_hash if e else None) for r in rows]
    chain_ok = all(items[i]["parent_revision_id"] == (items[i - 1]["id"] if i else None) for i in range(len(items)))
    return {"event_id": event_id, "event_present": e is not None, "raw_hash": e.raw_hash if e else None,
            "revisions": items, "count": len(items),
            "current_revision": next((i["revision_no"] for i in items if i["is_current"]), None),
            "integrity": {"all_snapshots_verified": all(i["snapshot_verified"] for i in items),
                          "raw_hash_unchanged": all(i["raw_hash_matches_event"] is not False for i in items),
                          "parent_chain_intact": chain_ok,
                          "single_current": sum(i["is_current"] for i in items) <= 1},
            "note": None if items else "No revisions: the event has never been reprocessed or replayed."}
