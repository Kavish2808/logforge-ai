"""Business logic for ingesting, listing, and reprocessing events.

Thin wrapper that ties the deterministic pipeline (app.pipeline) to the
repository layer (app.db.repository) and converts ORM rows to the public
UniversalEvent schema. API routes should only call into this module —
this is where batch orchestration, per-item failure isolation, and
defensive persistence guards live.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.repository import event_repo
from app.pipeline.hashing import sha256_hex
from app.pipeline.orchestrator import PipelineResult
from app.pipeline.orchestrator import process as run_pipeline
from app.schema.ocsf import EventStatus, FormatType, UniversalEvent

logger = logging.getLogger(__name__)

# Defensive length guard applied at the persistence boundary. Most of these
# values are adapter-authored (short, trusted YAML strings), but severity
# and product_version can originate directly from parsed log content, which
# is attacker/vendor controlled and has no inherent length limit. Rather
# than trust every adapter author to never map an unbounded field here, we
# clamp centrally so a DB column-length violation can never turn into a
# lost event or an unhandled 500.
_VARCHAR_LIMITS: dict[str, int] = {
    "vendor": 128,
    "product": 128,
    "product_version": 64,
    "adapter_id": 128,
    "adapter_version": 32,
    "ocsf_class_name": 128,
    "ocsf_category_name": 128,
    "event_type": 128,
    "severity": 32,
    "format_detected": 32,
}


class BatchIngestResult:
    __slots__ = ("results", "total", "success_count", "partial_count", "failed_count")

    def __init__(self, results: list[UniversalEvent]):
        self.results = results
        self.total = len(results)
        self.success_count = sum(1 for r in results if r.status == EventStatus.SUCCESS)
        self.partial_count = sum(1 for r in results if r.status == EventStatus.PARTIAL)
        self.failed_count = sum(1 for r in results if r.status == EventStatus.FAILED)


def ingest_raw_log(db: Session, raw_log: str) -> UniversalEvent:
    """Ingest a single raw log. Never raises: any unexpected failure in the
    pipeline or the database write still results in a persisted, FAILED
    event carrying the raw log and its hash, rather than a lost event or
    an unhandled exception propagating to the API layer."""
    try:
        result = run_pipeline(raw_log)
        fields = _pipeline_result_fields(result)
        event = Event(event_id=generate_event_id(), **fields)
        saved = event_repo.create_event(db, event)
        return UniversalEvent.model_validate(saved, from_attributes=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error ingesting a log; falling back to raw-only preservation.")
        db.rollback()
        return _record_pipeline_exception(db, raw_log, exc)


def ingest_batch(db: Session, raw_logs: list[str]) -> BatchIngestResult:
    """Ingests each log independently: one bad or even crashing log can
    never abort the rest of the batch, because ingest_raw_log itself never
    raises and always commits its own event."""
    results = [ingest_raw_log(db, raw_log) for raw_log in raw_logs]
    return BatchIngestResult(results)


def get_event(db: Session, event_id: str) -> Event | None:
    return event_repo.get_event(db, event_id)


def list_events(
    db: Session,
    *,
    vendor: str | None = None,
    status: str | None = None,
    format_detected: str | None = None,
    adapter_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[UniversalEvent], int]:
    rows, total = event_repo.list_events(
        db,
        vendor=vendor,
        status=status,
        format_detected=format_detected,
        adapter_id=adapter_id,
        limit=limit,
        offset=offset,
    )
    return [UniversalEvent.model_validate(row, from_attributes=True) for row in rows], total


def reprocess_event(db: Session, event: Event) -> UniversalEvent:
    result = run_pipeline(event.raw_event, received_at=event.received_at)
    fields = _pipeline_result_fields(result)
    fields.pop("received_at", None)  # never overwrite the original receipt time
    updated = event_repo.update_event_fields(db, event, fields)
    return UniversalEvent.model_validate(updated, from_attributes=True)


def _record_pipeline_exception(db: Session, raw_log: str, exc: Exception) -> UniversalEvent:
    """Persists a raw log as a FAILED event when something raised an
    exception the deterministic pipeline itself is not supposed to raise
    (a bug, or a database-level rejection). The raw log and its hash are
    never lost, even when our own code has a defect."""
    now = datetime.now(tz=timezone.utc)
    event = Event(
        event_id=generate_event_id(),
        raw_event=raw_log,
        raw_hash=sha256_hex(raw_log),
        received_at=now,
        processed_at=now,
        format_detected=FormatType.UNKNOWN.value,
        status=EventStatus.FAILED.value,
        extensions={},
        processing_metadata={"pipeline_version": "0.1.0"},
        warnings=[],
        error_message=f"Unexpected pipeline error ({type(exc).__name__}). See server logs for details.",
    )
    saved = event_repo.create_event(db, event)
    return UniversalEvent.model_validate(saved, from_attributes=True)


def _clamp_varchar_fields(fields: dict[str, Any]) -> None:
    warnings: list[str] = list(fields.get("warnings") or [])
    truncated = False
    for key, max_len in _VARCHAR_LIMITS.items():
        value = fields.get(key)
        if isinstance(value, str) and len(value) > max_len:
            fields[key] = value[:max_len]
            warnings.append(f"Field '{key}' exceeded {max_len} characters and was truncated.")
            truncated = True
    fields["warnings"] = warnings
    # Truncation is lossy by definition — a SUCCESS status must not hide it.
    if truncated and fields.get("status") == EventStatus.SUCCESS.value:
        fields["status"] = EventStatus.PARTIAL.value


def _pipeline_result_fields(result: PipelineResult) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "raw_event": result.raw_event,
        "raw_hash": result.raw_hash,
        "received_at": result.received_at,
        "processed_at": result.processed_at,
        "event_timestamp": result.event_timestamp,
        "format_detected": result.format_detected,
        "vendor": result.vendor,
        "product": result.product,
        "product_version": result.product_version,
        "adapter_id": result.adapter_id,
        "adapter_version": result.adapter_version,
        "ocsf_class_uid": result.ocsf_class_uid,
        "ocsf_class_name": result.ocsf_class_name,
        "ocsf_category_uid": result.ocsf_category_uid,
        "ocsf_category_name": result.ocsf_category_name,
        "event_type": result.event_type,
        "event_action": result.event_action,
        "severity": result.severity,
        "severity_id": result.severity_id,
        "status": result.status,
        "network": result.network,
        "user": result.user,
        "process": result.process,
        "extensions": result.extensions,
        "normalized_event": result.normalized_event,
        "processing_metadata": result.processing_metadata,
        "structural_fingerprint": result.structural_fingerprint,
        "warnings": result.warnings,
        "error_message": result.error_message,
    }
    _clamp_varchar_fields(fields)
    return fields
