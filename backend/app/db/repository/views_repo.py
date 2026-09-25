"""Read-only query functions for the operational intelligence layer.

Only SELECT statements built with SQLAlchemy expressions (parameterized).
Nothing here adds, updates or deletes; the views API additionally runs every
request in a READ ONLY database transaction.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.db.models.onboarding import OnboardedAdapter, OnboardingSession
from app.db.models.source_baseline import SourceBaseline, SourceBaselineHistory

MAX_DISTINCT = 200

# Drift record fields inside the processing_metadata JSONB column.
DRIFT_STATUS = Event.processing_metadata["drift"]["status"].astext
DRIFT_SEVERITY = Event.processing_metadata["drift"]["severity"].astext
DRIFT_SOURCE = Event.processing_metadata["drift"]["source_key"].astext
ADAPTER_SOURCE = Event.processing_metadata["adapter_source"].astext


class CursorError(ValueError):
    pass


@dataclass
class EventFilters:
    start: datetime | None = None
    end: datetime | None = None
    status: list[str] | None = None
    source: str | None = None
    vendor: str | None = None
    product: str | None = None
    format: str | None = None
    adapter_id: str | None = None
    adapter_version: str | None = None
    drift_status: str | None = None
    drift_severity: str | None = None
    severity: str | None = None
    category: str | None = None
    search: str | None = None


def _conditions(f: EventFilters) -> list[ColumnElement[bool]]:
    c: list[ColumnElement[bool]] = []
    if f.start:
        c.append(Event.received_at >= f.start)
    if f.end:
        c.append(Event.received_at < f.end)
    if f.status:
        c.append(Event.status.in_(f.status))
    if f.source:
        # A source is its adapter; POSSIBLE_FORMAT_DRIFT events (routed to a
        # generic adapter) belong to the vendor source they are suspected of.
        c.append(or_(Event.adapter_id == f.source, DRIFT_SOURCE == f.source))
    if f.vendor:
        c.append(Event.vendor == f.vendor)
    if f.product:
        c.append(Event.product == f.product)
    if f.format:
        c.append(Event.format_detected == f.format)
    if f.adapter_id:
        c.append(Event.adapter_id == f.adapter_id)
    if f.adapter_version:
        c.append(Event.adapter_version == f.adapter_version)
    if f.drift_status:
        c.append(DRIFT_STATUS.is_(None) if f.drift_status == "NONE" else DRIFT_STATUS == f.drift_status)
    if f.drift_severity:
        c.append(DRIFT_SEVERITY == f.drift_severity)
    if f.severity:
        c.append(Event.severity == f.severity)
    if f.category:
        c.append(Event.ocsf_category_name == f.category)
    if f.search:
        term = f.search.strip()
        if len(term) == 26 and term.isalnum():
            c.append(Event.event_id == term.upper())
        elif len(term) == 64 and all(ch in "0123456789abcdef" for ch in term.lower()):
            c.append(Event.raw_hash == term.lower())
        else:
            c.append(Event.raw_event.icontains(term, autoescape=True))
    return c


def encode_cursor(received_at: datetime, event_id: str) -> str:
    raw = json.dumps({"t": received_at.isoformat(), "i": event_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["t"]), str(data["i"])
    except (binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CursorError("Invalid cursor.") from exc


def event_page(db: Session, f: EventFilters, *, limit: int, cursor: str | None) -> tuple[list[Event], str | None, bool]:
    """Keyset pagination on (received_at DESC, event_id DESC): stable under
    concurrent inserts, O(limit) per page regardless of depth."""
    stmt = select(Event).where(*_conditions(f))
    if cursor:
        ts, eid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Event.received_at, Event.event_id) < tuple_(ts, eid))
    stmt = stmt.order_by(Event.received_at.desc(), Event.event_id.desc()).limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].received_at, rows[-1].event_id) if has_more and rows else None
    return rows, next_cursor, has_more


def count_events(db: Session, f: EventFilters) -> int:
    return db.execute(select(func.count()).select_from(Event).where(*_conditions(f))).scalar_one()


def grouped_counts(db: Session, f: EventFilters, column, *, limit: int | None = None) -> dict[str, int]:
    stmt = (
        select(column, func.count())
        .select_from(Event)
        .where(*_conditions(f))
        .group_by(column)
        .order_by(func.count().desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return {("NONE" if k is None else str(k)): n for k, n in db.execute(stmt).all()}


def distinct_count(db: Session, f: EventFilters, column) -> int:
    return db.execute(select(func.count(func.distinct(column))).select_from(Event).where(*_conditions(f))).scalar_one()


def trend(db: Session, f: EventFilters, bucket: str, *, max_buckets: int = 500) -> list[dict[str, Any]]:
    b = func.date_trunc(bucket, Event.received_at).label("bucket")
    stmt = (
        select(b, Event.status, func.count())
        .where(*_conditions(f))
        .group_by(b, Event.status)
        .order_by(b.desc())
        .limit(max_buckets * 6)
    )
    buckets: dict[datetime, dict[str, int]] = {}
    for at, status, n in db.execute(stmt).all():
        buckets.setdefault(at, {})[status] = n
    ordered = sorted(buckets.items())[-max_buckets:]
    return [{"bucket": at, "by_status": counts, "total": sum(counts.values())} for at, counts in ordered]


def distinct_values(db: Session, column) -> list[str]:
    stmt = select(column).where(column.is_not(None)).distinct().order_by(column).limit(MAX_DISTINCT)
    return [str(v) for v in db.execute(stmt).scalars().all() if v is not None]


def adapter_versions(db: Session) -> list[dict[str, Any]]:
    stmt = (
        select(Event.adapter_id, Event.adapter_version, func.count())
        .where(Event.adapter_id.is_not(None))
        .group_by(Event.adapter_id, Event.adapter_version)
        .order_by(Event.adapter_id, Event.adapter_version)
        .limit(MAX_DISTINCT)
    )
    return [{"adapter_id": a, "adapter_version": v, "events": n} for a, v, n in db.execute(stmt).all()]


def get_event(db: Session, event_id: str) -> Event | None:
    return db.get(Event, event_id)


# --- sources ---------------------------------------------------------------------------------


def source_keys(db: Session) -> list[str]:
    keys = set(distinct_values(db, Event.adapter_id))
    keys |= set(db.execute(select(SourceBaseline.source_key)).scalars().all())
    keys |= set(db.execute(select(OnboardedAdapter.adapter_id).distinct()).scalars().all())
    return sorted(keys)


def source_event_stats(db: Session, key: str) -> dict[str, Any]:
    by_adapter = Event.adapter_id == key
    status = dict(db.execute(select(Event.status, func.count()).where(by_adapter).group_by(Event.status)).all())
    formats = dict(db.execute(select(Event.format_detected, func.count()).where(by_adapter).group_by(Event.format_detected)).all())
    versions = {
        (v or "NONE"): n
        for v, n in db.execute(select(Event.adapter_version, func.count()).where(by_adapter).group_by(Event.adapter_version)).all()
    }
    drift = {
        (s or "NONE"): n
        for s, n in db.execute(
            select(DRIFT_STATUS, func.count()).where(or_(by_adapter, DRIFT_SOURCE == key)).group_by(DRIFT_STATUS)
        ).all()
    }
    under_review = db.execute(
        select(func.count()).select_from(Event).where(or_(by_adapter, DRIFT_SOURCE == key), Event.status == "UNDER_REVIEW")
    ).scalar_one()
    latest = db.execute(
        select(Event.vendor, Event.product, Event.received_at).where(by_adapter).order_by(Event.received_at.desc()).limit(1)
    ).first()
    return {"status": status, "formats": formats, "versions": versions, "drift": drift,
            "under_review": under_review, "latest": latest}


def baseline(db: Session, key: str) -> SourceBaseline | None:
    return db.get(SourceBaseline, key)


def baseline_history(db: Session, key: str) -> list[SourceBaselineHistory]:
    stmt = select(SourceBaselineHistory).where(SourceBaselineHistory.source_key == key).order_by(
        SourceBaselineHistory.version, SourceBaselineHistory.id)
    return list(db.execute(stmt).scalars().all())


def onboarded_versions(db: Session, key: str) -> list[OnboardedAdapter]:
    stmt = select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == key).order_by(OnboardedAdapter.version)
    return list(db.execute(stmt).scalars().all())


def onboarded_version(db: Session, key: str, version: int) -> OnboardedAdapter | None:
    stmt = select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == key, OnboardedAdapter.version == version)
    return db.execute(stmt).scalars().first()


def learning_sessions_for_source(db: Session, key: str) -> list[LearningSession]:
    stmt = select(LearningSession).where(LearningSession.source_key == key).order_by(LearningSession.created_at)
    return list(db.execute(stmt).scalars().all())


def onboarding_sessions_for_adapter(db: Session, key: str) -> list[OnboardingSession]:
    """Sessions approved as this adapter, or whose last validation previewed it."""
    preview_id = OnboardingSession.validation["adapter_preview"]["id"].astext
    stmt = (
        select(OnboardingSession)
        .where(or_(OnboardingSession.adapter_id == key, preview_id == key))
        .order_by(OnboardingSession.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def recent_drift_events(db: Session, key: str, limit: int = 50) -> list[Event]:
    stmt = (
        select(Event)
        .where(or_(Event.adapter_id == key, DRIFT_SOURCE == key),
               DRIFT_STATUS.in_(["DRIFT", "POSSIBLE_FORMAT_DRIFT"]))
        .order_by(Event.received_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


# --- lineage references -----------------------------------------------------------------------


def learning_sessions_referencing(db: Session, event_id: str) -> list[LearningSession]:
    """Learning sessions triggered by, or using as evidence, this event."""
    stmt = select(LearningSession).where(or_(
        LearningSession.trigger_event_id == event_id,
        LearningSession.evidence.contains({"drifted": [{"event_id": event_id}]}),
        LearningSession.evidence.contains({"historical": [{"event_id": event_id}]}),
    )).order_by(LearningSession.created_at)
    return list(db.execute(stmt).scalars().all())


def onboarding_sessions_referencing(db: Session, event_id: str) -> list[OnboardingSession]:
    stmt = select(OnboardingSession).where(
        OnboardingSession.samples.contains([{"source_event_id": event_id}])
    ).order_by(OnboardingSession.created_at)
    return list(db.execute(stmt).scalars().all())


def counts_by(db: Session, model, column) -> dict[str, int]:
    return {str(k): n for k, n in db.execute(select(column, func.count()).select_from(model).group_by(column)).all()}


def active_onboarded_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(OnboardedAdapter).where(OnboardedAdapter.status == "ACTIVE")).scalar_one()


def baselines_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(SourceBaseline)).scalar_one()
