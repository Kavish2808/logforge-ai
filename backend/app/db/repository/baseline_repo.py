"""Data-access layer for the `source_baselines` table (Phase 5 drift detection).

None of these functions commit: baseline writes always share the
transaction of the event write they belong to (the service commits).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.source_baseline import ORIGIN_AUTO_BOOTSTRAP, SourceBaseline, SourceBaselineHistory
from app.schema.ocsf import EventStatus


def get_baseline(db: Session, source_key: str) -> SourceBaseline | None:
    return db.get(SourceBaseline, source_key)


def insert_if_absent(
    db: Session,
    *,
    source_key: str,
    adapter_id: str,
    adapter_version: str | None,
    format_detected: str,
    fingerprint: dict[str, Any],
    created_from_event_id: str | None,
) -> tuple[SourceBaseline, bool]:
    """Race-safe bootstrap: if two first events for the same source arrive
    concurrently, exactly one row is created and both compare against it.
    Returns (baseline, created)."""
    stmt = (
        insert(SourceBaseline)
        .values(
            source_key=source_key,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            format_detected=format_detected,
            fingerprint=fingerprint,
            accepted_variants=[],
            origin=ORIGIN_AUTO_BOOTSTRAP,
            version=1,
            created_from_event_id=created_from_event_id,
        )
        .on_conflict_do_nothing(index_elements=["source_key"])
        # RETURNING yields a row only if this statement inserted it
        # (rowcount is not reliable for ORM-entity inserts via Session).
        .returning(SourceBaseline.source_key)
    )
    created = db.execute(stmt).first() is not None
    baseline = db.get(SourceBaseline, source_key)
    assert baseline is not None
    return baseline, created


def list_baselines(db: Session) -> list[SourceBaseline]:
    return list(db.execute(select(SourceBaseline).order_by(SourceBaseline.source_key)).scalars().all())


def under_review_counts(db: Session, source_keys: list[str] | None = None) -> dict[str, int]:
    """Number of UNDER_REVIEW events per known source, keyed by the drift
    record's source_key — so POSSIBLE_FORMAT_DRIFT events routed to a
    generic adapter count toward the vendor source they are suspected of."""
    drift_source = Event.processing_metadata["drift"]["source_key"].astext
    stmt = (
        select(drift_source, func.count())
        .where(Event.status == EventStatus.UNDER_REVIEW.value)
        .group_by(drift_source)
    )
    if source_keys is not None:
        stmt = stmt.where(drift_source.in_(source_keys))
    return {key: count for key, count in db.execute(stmt).all() if key is not None}


def add_history(
    db: Session,
    *,
    source_key: str,
    version: int,
    action: str,
    fingerprint: dict[str, Any],
    event_id: str | None,
    changes: dict[str, Any] | None = None,
    note: str | None = None,
) -> SourceBaselineHistory:
    entry = SourceBaselineHistory(
        source_key=source_key,
        version=version,
        action=action,
        event_id=event_id,
        signature=fingerprint.get("signature"),
        field_count=fingerprint.get("field_count"),
        changes=changes,
        note=note,
    )
    db.add(entry)
    return entry


def list_history(db: Session, source_key: str) -> list[SourceBaselineHistory]:
    stmt = (
        select(SourceBaselineHistory)
        .where(SourceBaselineHistory.source_key == source_key)
        .order_by(SourceBaselineHistory.version, SourceBaselineHistory.id)
    )
    return list(db.execute(stmt).scalars().all())
