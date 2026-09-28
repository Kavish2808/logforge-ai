"""Phase 8 Step 3: compact, exception-based lineage.

One `event_lineage_compact` row per event (two integers, see
app.phase8.lineage_codec) written through the Phase 7 persist hook (H3), in
the event's own transaction and SAVEPOINT. The detailed lineage API
(GET /views/events/{id}/lineage) is untouched and remains the source of
truth; the compact row is a point-in-time projection of it:

- stage outcomes use exactly the rules of views_service.lineage (the same
  helpers are reused, never re-implemented differently);
- FAILED / PARTIAL / DRIFT / OVERFLOW / VAULT_FAILED are derived from the
  event and its Phase 7 evidence rows;
- LEARNING / ROLLBACK / REPLAY belong to their Phase 8 services
  (`mark_exception`) and survive every recomputation.

A reprocess hook fires before the reprocessed fields are written, so the
row is recomputed right before that transaction commits (never from stale
fields). Events without a row (pre-Phase-8, or a hook failure) are filled by
the `compact_lineage_backfill` scheduler step; the read API computes them on
the fly and says so (`persisted: false`). Nothing is ever silently dropped.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.evidence import RAW_FAILED, EventExtensionOverflow, EventRawStorage
from app.db.models.phase8 import EventLineageCompact
from app.db.repository import views_repo
from app.phase8 import lineage_codec as codec
from app.pipeline.hashing import sha256_hex
from app.services import views_service

logger = logging.getLogger(__name__)

HOOK_NAME = "compact_lineage"
STEP_NAME = "compact_lineage_backfill"
BACKFILL_BATCH = 500
_PENDING = "phase8_lineage_pending"
_LISTENING = "phase8_lineage_listening"
_DRIFT_EXCEPTION_STATUSES = ("DRIFT", "POSSIBLE_FORMAT_DRIFT")


class LineageNotFound(Exception):
    pass


# --------------------------------------------------------------------------
# Computation (mirrors views_service.lineage stage-for-stage)
# --------------------------------------------------------------------------


def stage_outcomes(db: Session, e: Event, overflow: EventExtensionOverflow | None = None) -> dict[str, str]:
    ok, warn, fail, skipped = codec.OK, codec.WARN, codec.FAIL, codec.SKIPPED
    pm = e.processing_metadata or {}
    drift = pm.get("drift") if isinstance(pm.get("drift"), dict) else None
    failed = e.status == "FAILED"
    stages: dict[str, str] = {}

    stages["RAW"] = ok if sha256_hex(e.raw_event) == e.raw_hash else fail
    stages["FORMAT"] = fail if e.format_detected == "unknown" else ok
    stages["PARSER"] = fail if failed else ok

    mapping, adapter_details = views_service._adapter_at_version(db, e)
    if e.adapter_id is None:
        stages["ADAPTER"] = skipped
    else:
        stages["ADAPTER"] = warn if adapter_details.get("superseded_since") else ok

    stages["NORMALIZATION"] = skipped if failed else (warn if e.status == "PARTIAL" else ok)

    if failed:
        stages["FIELD_ACCOUNTING"] = skipped
    else:
        parsed = list((e.structural_fingerprint or {}).get("field_order") or [])
        accounting = views_service._field_accounting(e, parsed, dict(e.extensions or {}), mapping,
                                                     dict(overflow.payload) if overflow is not None else {})
        stages["FIELD_ACCOUNTING"] = fail if accounting["unaccounted"] else ok

    stages["WARNINGS"] = warn if (e.warnings or []) else ok

    if drift is None:
        stages["DRIFT"] = skipped
    else:
        stages["DRIFT"] = {"NORMAL": ok, "BASELINE_CREATED": ok, "ERROR": fail}.get(drift.get("status"), warn)

    key = (drift or {}).get("source_key") or e.adapter_id
    base = views_repo.baseline(db, key) if key else None
    if base is None:
        stages["BASELINE"] = skipped
    else:
        relation = views_service._baseline_relation(e.structural_fingerprint or {}, base)
        stages["BASELINE"] = ok if relation != "not accepted" else warn
    return stages


def derived_exceptions(e: Event, overflow: EventExtensionOverflow | None,
                       storage: EventRawStorage | None) -> list[str]:
    pm = e.processing_metadata or {}
    drift = pm.get("drift") if isinstance(pm.get("drift"), dict) else {}
    out: list[str] = []
    if e.status == "FAILED":
        out.append("FAILED")
    if e.status == "PARTIAL":
        out.append("PARTIAL")
    if drift.get("status") in _DRIFT_EXCEPTION_STATUSES:
        out.append("DRIFT")
    if overflow is not None:
        out.append("OVERFLOW")
    if storage is not None and storage.status == RAW_FAILED:
        out.append("VAULT_FAILED")
    return out


def compute(db: Session, e: Event, *, fresh: bool = False) -> tuple[int, int]:
    """(stage_mask, derived exception_mask) for the event as it is now.

    `fresh`: the event was created in this transaction. Phase 7 then writes an
    overflow row if and only if the event is marked SPILLED, so the overflow
    lookup is skipped for the (common) inline case."""
    spilled = ((e.processing_metadata or {}).get("extension_spill") or {}).get("mode") == "SPILLED"
    overflow = db.get(EventExtensionOverflow, e.event_id) if spilled or not fresh else None
    storage = db.get(EventRawStorage, e.event_id)
    return (codec.encode_stages(stage_outcomes(db, e, overflow)),
            codec.encode_exceptions(derived_exceptions(e, overflow, storage)))


_UPSERT = text(
    "INSERT INTO event_lineage_compact (event_id, template_version, stage_mask, exception_mask, is_exception, computed_at) "
    "VALUES (:event_id, :version, :stage_mask, :exception_mask, :is_exception, :computed_at) "
    "ON CONFLICT (event_id) DO UPDATE SET "
    "template_version = EXCLUDED.template_version, stage_mask = EXCLUDED.stage_mask, "
    # Service-owned bits (LEARNING / ROLLBACK / REPLAY) are kept across recomputation.
    "exception_mask = EXCLUDED.exception_mask | (event_lineage_compact.exception_mask & :keep), "
    "is_exception = EXCLUDED.is_exception OR ((event_lineage_compact.exception_mask & :keep) <> 0), "
    "computed_at = EXCLUDED.computed_at"
)


def upsert(db: Session, e: Event, *, fresh: bool = False) -> tuple[int, int]:
    stage_mask, derived = compute(db, e, fresh=fresh)
    db.execute(_UPSERT, {"event_id": e.event_id, "version": codec.TEMPLATE_VERSION, "stage_mask": stage_mask,
                         "exception_mask": derived, "is_exception": codec.decode(stage_mask, derived).is_exception,
                         "computed_at": datetime.now(tz=timezone.utc), "keep": codec.SERVICE_EXCEPTION_MASK})
    return stage_mask, derived


# --------------------------------------------------------------------------
# H3 persist hook
# --------------------------------------------------------------------------


def persist_hook(db: Session, event: Event, reprocessed: bool) -> None:
    if not reprocessed:
        upsert(db, event, fresh=True)
        return
    # persist_reprocessed runs before the reprocessed fields are assigned; the
    # row is recomputed when that transaction commits.
    db.info.setdefault(_PENDING, {})[event.event_id] = event
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
    db.flush()  # the reprocessed fields and the rewritten overflow row
    for event in pending.values():
        savepoint = db.begin_nested()
        try:
            upsert(db, event)
            savepoint.commit()
        except Exception:  # noqa: BLE001 — the reprocessed event must commit regardless
            savepoint.rollback()
            logger.exception("Compact lineage recompute failed for event %s; backfill will retry.", event.event_id)


def _drop_pending(db: Session, previous_transaction) -> None:
    # Only an outermost rollback discards the reprocess; a savepoint rollback does not.
    if previous_transaction.parent is None and not previous_transaction.nested:
        db.info.pop(_PENDING, None)


# --------------------------------------------------------------------------
# Service-owned exception bits
# --------------------------------------------------------------------------


def mark_exception(db: Session, event_ids: list[str], name: str) -> int:
    """Set LEARNING / ROLLBACK / REPLAY on existing rows (no commit). Events
    without a row get one computed first, so the bit is never lost."""
    if name not in codec.SERVICE_EXCEPTIONS:
        raise ValueError(f"'{name}' is derived from the event, not set by a service.")
    bit = codec.exception_bit(name)
    for event_id in dict.fromkeys(event_ids):
        if db.get(EventLineageCompact, event_id) is None:
            e = db.get(Event, event_id)
            if e is None:
                continue
            upsert(db, e)
        db.execute(text("UPDATE event_lineage_compact SET exception_mask = exception_mask | :b, is_exception = true "
                        "WHERE event_id = :i"), {"b": bit, "i": event_id})
    return bit


# --------------------------------------------------------------------------
# Backfill (scheduler step)
# --------------------------------------------------------------------------


def backfill(db: Session, *, limit: int = BACKFILL_BATCH) -> dict[str, int]:
    rows = db.execute(
        select(Event).outerjoin(EventLineageCompact, EventLineageCompact.event_id == Event.event_id)
        .where(EventLineageCompact.event_id.is_(None)).order_by(Event.received_at, Event.event_id).limit(limit)
    ).scalars().all()
    written = failed = 0
    for e in rows:
        savepoint = db.begin_nested()
        try:
            upsert(db, e)
            savepoint.commit()
            written += 1
        except Exception:  # noqa: BLE001
            savepoint.rollback()
            failed += 1
            logger.exception("Compact lineage backfill failed for event %s.", e.event_id)
    db.commit()
    return {"written": written, "failed": failed}


# --------------------------------------------------------------------------
# Read API
# --------------------------------------------------------------------------


def _decoded(stage_mask: int, exception_mask: int, template_version: int | None) -> dict[str, Any]:
    try:
        lin = codec.decode(stage_mask, exception_mask, template_version=template_version)
    except codec.LineageDecodeError as exc:
        return {"decodable": False, "error": str(exc)}
    return {
        "decodable": True,
        "stages": [{"stage": s, "outcome": lin.stages[s]} for s in codec.STAGES],
        "exceptions": list(lin.exceptions),
        "is_exception": lin.is_exception,
        "worst_outcome": lin.worst,
        "packed_hex": codec.pack(stage_mask, exception_mask).hex(),
    }


def get(db: Session, event_id: str, *, verify: bool = False) -> dict[str, Any]:
    e = db.get(Event, event_id)
    if e is None:
        raise LineageNotFound(f"Event '{event_id}' not found")
    row = db.get(EventLineageCompact, event_id)
    if row is not None:
        out = {"event_id": event_id, "persisted": True, "template_version": row.template_version,
               "stage_mask": row.stage_mask, "exception_mask": row.exception_mask,
               "is_exception_column": row.is_exception, "computed_at": row.computed_at,
               **_decoded(row.stage_mask, row.exception_mask, row.template_version)}
    else:
        stage_mask, derived = compute(db, e)
        out = {"event_id": event_id, "persisted": False, "template_version": codec.TEMPLATE_VERSION,
               "stage_mask": stage_mask, "exception_mask": derived, "is_exception_column": None,
               "computed_at": datetime.now(tz=timezone.utc),
               "note": "No stored row yet (pre-Phase-8 event or pending backfill); computed on read, not stored.",
               **_decoded(stage_mask, derived, None)}
    out["detailed_lineage"] = f"/api/v1/views/events/{event_id}/lineage"
    out["not_in_compact_form"] = ["per-stage summaries and details", "LEARNING_HISTORY stage (see LEARNING bit)",
                                  "field-level accounting", "Phase 7 evidence facts"]
    if verify and out.get("decodable"):
        out["verification"] = verify_against_detailed(db, e, out)
    return out


def verify_against_detailed(db: Session, e: Event, compact: dict[str, Any]) -> dict[str, Any]:
    """Compare the compact row with a fresh detailed lineage. A difference is
    reported, never hidden: the detailed lineage is computed at read time, so
    a later baseline/adapter change legitimately makes an older row stale."""
    detailed = codec.from_detailed_chain(views_service.lineage(db, e.event_id)["chain"])
    stored = {s["stage"]: s["outcome"] for s in compact["stages"]}
    diffs = [{"stage": s, "compact": stored[s], "detailed_now": detailed[s]} for s in codec.STAGES if stored[s] != detailed[s]]
    now_stage_mask, now_derived = compute(db, e)
    stored_derived = compact["exception_mask"] & ~codec.SERVICE_EXCEPTION_MASK
    return {"equivalent": not diffs and stored_derived == now_derived, "stage_differences": diffs,
            "derived_exceptions_now": list(codec.decode(now_stage_mask, now_derived).exceptions),
            "stale": bool(diffs) or stored_derived != now_derived}


def stats(db: Session) -> dict[str, Any]:
    total_events = db.execute(select(func.count()).select_from(Event)).scalar_one()
    rows, exceptional = db.execute(select(func.count(), func.count().filter(EventLineageCompact.is_exception))).one()
    by_bit = {}
    for i, name in enumerate(codec.EXCEPTIONS):
        by_bit[name] = db.execute(select(func.count()).select_from(EventLineageCompact).where(
            EventLineageCompact.exception_mask.op("&")(1 << i) != 0)).scalar_one()
    by_stage: dict[str, dict[str, int]] = {}
    for i, name in enumerate(codec.STAGES):
        # The shift count is a module constant (PostgreSQL's >> takes an integer, not a bound bigint).
        counts = dict(db.execute(text(f"SELECT (stage_mask >> {codec.STAGE_BITS * i}) & 3, count(*) "
                                      "FROM event_lineage_compact GROUP BY 1")).all())
        by_stage[name] = {codec.OUTCOMES[c]: n for c, n in sorted(counts.items()) if 0 <= c < 4}
    versions = dict(db.execute(select(EventLineageCompact.template_version, func.count())
                               .group_by(EventLineageCompact.template_version)).all())
    table_bytes = db.execute(text("SELECT pg_total_relation_size('event_lineage_compact')")).scalar_one()
    return {
        "template_version": codec.TEMPLATE_VERSION,
        "events_total": total_events,
        "rows": rows,
        "missing_rows": max(total_events - rows, 0),
        "exception_rows": exceptional,
        "normal_rows": rows - exceptional,
        "by_exception": by_bit,
        "by_stage_outcome": by_stage,
        "by_template_version": {str(k): v for k, v in versions.items()},
        "table_total_bytes": int(table_bytes),
    }


def benchmark(db: Session, *, sample: int = 50) -> dict[str, Any]:
    """Measured storage cost: compact row vs. materializing the detailed
    lineage, on the most recent `sample` events that have a compact row.
    Sizes come from PostgreSQL itself (pg_column_size); nothing is estimated."""
    ids = db.execute(select(EventLineageCompact.event_id).join(Event, Event.event_id == EventLineageCompact.event_id)
                     .order_by(Event.received_at.desc(), Event.event_id.desc()).limit(sample)).scalars().all()
    if not ids:
        return {"sample": 0, "note": "No compact lineage rows to measure yet."}
    row_bytes = db.execute(text(
        "SELECT pg_column_size(t.*) FROM event_lineage_compact t WHERE t.event_id = ANY(:ids)"), {"ids": list(ids)}
    ).scalars().all()
    payload_bytes = db.execute(text(
        "SELECT pg_column_size(t.template_version) + pg_column_size(t.stage_mask) + pg_column_size(t.exception_mask) "
        "+ pg_column_size(t.is_exception) FROM event_lineage_compact t WHERE t.event_id = ANY(:ids)"), {"ids": list(ids)}
    ).scalars().all()
    detailed_json: list[int] = []
    detailed_jsonb: list[int] = []
    started = datetime.now(tz=timezone.utc)
    for event_id in ids:
        doc = json.dumps(views_service.lineage(db, event_id), default=str, separators=(",", ":"), sort_keys=True)
        detailed_json.append(len(doc.encode("utf-8")))
        detailed_jsonb.append(db.execute(text("SELECT pg_column_size(CAST(:d AS jsonb))"), {"d": doc}).scalar_one())
    detailed_ms = (datetime.now(tz=timezone.utc) - started).total_seconds() * 1000 / len(ids)
    started = datetime.now(tz=timezone.utc)
    for event_id in ids:
        compute(db, db.get(Event, event_id))
    compact_ms = (datetime.now(tz=timezone.utc) - started).total_seconds() * 1000 / len(ids)
    avg_row = statistics.mean(row_bytes)
    avg_jsonb = statistics.mean(detailed_jsonb)
    return {
        "sample": len(ids),
        "compact": {"packed_bytes": codec.PACKED_BYTES, "column_payload_bytes_avg": statistics.mean(payload_bytes),
                    "row_bytes_avg": avg_row, "row_bytes_max": max(row_bytes),
                    "compute_ms_avg": round(compact_ms, 3)},
        "detailed": {"json_bytes_avg": statistics.mean(detailed_json), "json_bytes_max": max(detailed_json),
                     "jsonb_bytes_avg": avg_jsonb, "jsonb_bytes_max": max(detailed_jsonb),
                     "build_ms_avg": round(detailed_ms, 3)},
        "ratio_detailed_jsonb_to_compact_row": round(avg_jsonb / avg_row, 2) if avg_row else None,
        "note": ("row_bytes = pg_column_size of the whole compact row (includes the 26-char event_id key and the "
                 "row header); the detailed lineage is not stored by LogForge — it is rebuilt on read, and this "
                 "measures what storing it as JSONB would cost. Index/TOAST overhead is excluded on both sides."),
    }
