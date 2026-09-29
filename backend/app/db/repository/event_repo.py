"""Data-access layer for the `events` table.

Keeps SQLAlchemy query construction out of the service layer.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.event import Event


def create_event(db: Session, event: Event, commit: bool = True) -> Event:
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event


def get_event(db: Session, event_id: str) -> Event | None:
    return db.get(Event, event_id)


def list_events(
    db: Session,
    *,
    vendor: str | None = None,
    status: str | None = None,
    format_detected: str | None = None,
    adapter_id: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Event], int]:
    stmt = select(Event)
    count_stmt = select(func.count()).select_from(Event)

    if vendor:
        stmt = stmt.where(Event.vendor == vendor)
        count_stmt = count_stmt.where(Event.vendor == vendor)
    if status:
        stmt = stmt.where(Event.status == status)
        count_stmt = count_stmt.where(Event.status == status)
    if format_detected:
        stmt = stmt.where(Event.format_detected == format_detected)
        count_stmt = count_stmt.where(Event.format_detected == format_detected)
    if adapter_id:
        stmt = stmt.where(Event.adapter_id == adapter_id)
        count_stmt = count_stmt.where(Event.adapter_id == adapter_id)
    if source:
        from sqlalchemy import or_
        source_cond = or_(Event.adapter_id == source, Event.vendor == source)
        stmt = stmt.where(source_cond)
        count_stmt = count_stmt.where(source_cond)
    if q and q.strip():
        from sqlalchemy import or_
        pattern = f"%{q.strip()}%"
        q_cond = or_(
            Event.event_id.ilike(pattern),
            Event.vendor.ilike(pattern),
            Event.adapter_id.ilike(pattern),
            Event.raw_event.ilike(pattern),
        )
        stmt = stmt.where(q_cond)
        count_stmt = count_stmt.where(q_cond)

    total = db.execute(count_stmt).scalar_one()

    stmt = stmt.order_by(Event.received_at.desc()).limit(limit).offset(offset)
    items = list(db.execute(stmt).scalars().all())

    return items, total


def update_event_fields(db: Session, event: Event, fields: dict[str, Any]) -> Event:
    for key, value in fields.items():
        setattr(event, key, value)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
