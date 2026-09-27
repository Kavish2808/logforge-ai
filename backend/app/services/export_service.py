"""Bounded, streaming event export (NDJSON / JSON) under the published
logforge.export.v1 contract.

Memory is bounded by EXPORT_BATCH_SIZE: events are read in keyset batches
from a dedicated READ ONLY session and serialized one at a time; the
response is never materialized in full. Each export is recorded in
export_log (who, filters, bound, rows, completion).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session, sessionmaker

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.governance import ExportLog
from app.db.repository import views_repo as repo
from app.evidence.canonical import canonical_sha256
from app.evidence.spill import merge
from app.export.schema import SCHEMA_VERSION
from app.services import evidence_service

logger = logging.getLogger(__name__)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def event_record(e: Event, overflow, storage, membership: dict[str, Any] | None, *, include_raw: bool) -> dict[str, Any]:
    pm = e.processing_metadata or {}
    drift = pm.get("drift") if isinstance(pm.get("drift"), dict) else None
    inline = e.extensions or {}
    extensions = merge(inline, overflow.payload if overflow else None, overflow.key_order if overflow else None)
    parsed = len((e.structural_fingerprint or {}).get("field_order") or [])
    preserved = len(extensions)
    normalized_content = {"status": e.status, "adapter_id": e.adapter_id, "adapter_version": e.adapter_version,
                          "normalized": e.normalized_event, "network": e.network, "user": e.user, "process": e.process,
                          "event_action": e.event_action, "severity": e.severity, "extensions": extensions,
                          "event_timestamp": _iso(e.event_timestamp), "warnings": e.warnings or []}
    raw_bytes = len(e.raw_event.encode("utf-8"))
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "event",
        "event_id": e.event_id,
        "received_at": _iso(e.received_at),
        "processed_at": _iso(e.processed_at),
        "event_timestamp": _iso(e.event_timestamp),
        "status": e.status,
        "format": e.format_detected,
        "source_key": (drift or {}).get("source_key") or e.adapter_id,
        "vendor": e.vendor,
        "product": e.product,
        "product_version": e.product_version,
        "adapter": {"id": e.adapter_id, "version": e.adapter_version, "source": pm.get("adapter_source")},
        "ocsf": {"class_uid": e.ocsf_class_uid, "class_name": e.ocsf_class_name,
                 "category_uid": e.ocsf_category_uid, "category_name": e.ocsf_category_name},
        "event_type": e.event_type,
        "event_action": e.event_action,
        "severity": e.severity,
        "severity_id": e.severity_id,
        "network": e.network or None,
        "user": e.user or None,
        "process": e.process or None,
        "normalized": e.normalized_event,
        "extensions": extensions,
        "extension_storage": {"mode": "SPILLED" if overflow else "INLINE", "inline_field_count": len(inline),
                              "overflow_field_count": overflow.field_count if overflow else 0,
                              "overflow_sha256": overflow.payload_sha256 if overflow else None},
        "field_accounting": {
            "parsed_count": parsed,
            "mapped_count": max(parsed - preserved, 0) if e.status != "FAILED" else 0,
            "preserved_count": preserved,
            "preserved_inline": len(inline),
            "preserved_overflow": overflow.field_count if overflow else 0,
            "method": "mapped = parsed fields not preserved in extensions (consumed by the adapter)",
        },
        "drift": None if drift is None else {"status": drift.get("status"), "severity": drift.get("severity"),
                                             "source_key": drift.get("source_key")},
        "warnings": e.warnings or [],
        "error_message": e.error_message,
        "raw": {
            "sha256": e.raw_hash, "byte_size": raw_bytes, "encoding": "utf-8",
            "payload": e.raw_event if include_raw else None,
            "vault": None if storage is None else {"backend": storage.backend, "object_key": storage.object_key,
                                                   "status": storage.status, "tier": storage.tier},
        },
        "integrity": {"algorithm": "SHA-256", "raw_sha256": e.raw_hash, "merkle": membership},
        "revision": {"revision_hash": canonical_sha256(normalized_content), "pipeline_version": pm.get("pipeline_version"),
                     "processed_at": _iso(e.processed_at), "adapter_version": e.adapter_version},
    }


def _batches(session: Session, f: repo.EventFilters, event_ids: list[str] | None, cursor: str | None,
             max_events: int, batch_size: int) -> Iterator[tuple[list[Event], bool, str | None]]:
    """Yields (events, has_more_overall, next_cursor_after_batch) keyset batches."""
    position = repo.decode_cursor(cursor) if cursor else None
    remaining = max_events
    while remaining > 0:
        take = min(batch_size, remaining)
        stmt = select(Event).where(*repo._conditions(f))
        if event_ids:
            stmt = stmt.where(Event.event_id.in_(event_ids))
        if position:
            stmt = stmt.where(tuple_(Event.received_at, Event.event_id) < tuple_(*position))
        rows = list(session.execute(stmt.order_by(Event.received_at.desc(), Event.event_id.desc()).limit(take + 1))
                    .scalars().all())
        more = len(rows) > take
        rows = rows[:take]
        if not rows:
            yield [], False, None
            return
        remaining -= len(rows)
        position = (rows[-1].received_at, rows[-1].event_id)
        has_more = more
        yield rows, has_more, repo.encode_cursor(*position) if has_more else None
        if not more:
            return


def start_log(db: Session, *, actor: str, role: str | None, fmt: str, filters: dict[str, Any], max_events: int,
              include_raw: bool) -> ExportLog:
    row = ExportLog(id=generate_event_id(), actor=actor, role=role, format=fmt, filters=filters,
                    max_events=max_events, include_raw=include_raw, status="STARTED", rows=0)
    db.add(row)
    db.commit()
    return row


def _finish_log(factory: sessionmaker, export_id: str, *, rows: int, has_more: bool | None, status: str) -> None:
    with factory() as s:
        row = s.get(ExportLog, export_id)
        if row is not None:
            row.rows, row.has_more, row.status = rows, has_more, status
            row.completed_at = datetime.now(tz=timezone.utc)
            s.commit()


def stream(bind, *, export_id: str, fmt: str, f: repo.EventFilters, event_ids: list[str] | None, cursor: str | None,
           max_events: int, batch_size: int, include_raw: bool, filters_echo: dict[str, Any]) -> Iterator[bytes]:
    factory = sessionmaker(bind=bind, autoflush=False, future=True)
    count = 0
    has_more = False
    next_cursor = None
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    session = factory()
    status = "FAILED"
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        if fmt == "json":
            head = {"schema_version": SCHEMA_VERSION, "export_id": export_id, "generated_at": generated_at,
                    "filters": filters_echo}
            yield (json.dumps(head)[:-1] + ',"items":[').encode("utf-8")
        first = True
        for rows, more, nxt in _batches(session, f, event_ids, cursor, max_events, batch_size):
            ids = [r.event_id for r in rows]
            overflow = evidence_service.overflow_map(session, ids)
            storage = evidence_service.raw_storage_map(session, ids)
            members = evidence_service.membership_map(session, ids)
            chunks = []
            for e in rows:
                rec = event_record(e, overflow.get(e.event_id), storage.get(e.event_id), members.get(e.event_id),
                                   include_raw=include_raw)
                line = json.dumps(rec, ensure_ascii=False, default=str)
                if fmt == "json":
                    chunks.append(("" if first else ",") + line)
                else:
                    chunks.append(line + "\n")
                first = False
            count += len(rows)
            has_more, next_cursor = more, nxt
            session.expunge_all()  # keep memory bounded across batches
            if chunks:
                yield "".join(chunks).encode("utf-8")
        tail = {"count": count, "has_more": has_more, "next_cursor": next_cursor}
        if fmt == "json":
            yield ("]," + json.dumps(tail)[1:]).encode("utf-8")
        else:
            yield (json.dumps({"schema_version": SCHEMA_VERSION, "record_type": "trailer", "export_id": export_id,
                               **tail, "complete": True, "generated_at": generated_at}) + "\n").encode("utf-8")
        status = "COMPLETED"
    except Exception:
        logger.exception("Export %s failed after %d rows.", export_id, count)
        raise
    finally:
        session.rollback()
        session.close()
        try:
            _finish_log(factory, export_id, rows=count, has_more=has_more, status=status)
        except Exception:  # noqa: BLE001
            logger.exception("Could not finalize export log %s", export_id)


def filters_dict(f: repo.EventFilters, event_ids: list[str] | None) -> dict[str, Any]:
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(f).items() if v not in (None, [], "")}
    if event_ids:
        out["event_ids"] = {"count": len(event_ids)}
    return out


def list_logs(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(select(ExportLog).order_by(ExportLog.started_at.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "actor": r.actor, "role": r.role, "format": r.format, "filters": r.filters,
             "max_events": r.max_events, "include_raw": r.include_raw, "status": r.status, "rows": r.rows,
             "has_more": r.has_more, "started_at": r.started_at, "completed_at": r.completed_at} for r in rows]


def stats(db: Session) -> dict[str, Any]:
    from sqlalchemy import func

    by_status = dict(db.execute(select(ExportLog.status, func.count()).group_by(ExportLog.status)).all())
    rows = db.execute(select(func.coalesce(func.sum(ExportLog.rows), 0))).scalar_one()
    last = db.execute(select(ExportLog.started_at).order_by(ExportLog.started_at.desc()).limit(1)).scalar()
    return {"exports": sum(by_status.values()), "by_status": by_status, "rows_exported": int(rows), "last_export_at": last}
