"""Data access for Phase 6 learning sessions and their evidence events.
Nothing here commits; the service owns transaction boundaries. All queries
are SQLAlchemy expressions (parameterized)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.learning import OPEN_STATES, LearningSession
from app.schema.ocsf import EventStatus

MAX_EVIDENCE_EVENTS = 50
# The fingerprint signature covers field names only (not order/types), so
# candidates are fetched from a bounded pool and filtered on exact structure.
CANDIDATE_POOL = 500


def add(db: Session, session: LearningSession) -> LearningSession:
    db.add(session)
    return session


def get(db: Session, session_id: str, *, lock: bool = False) -> LearningSession | None:
    """`lock=True` takes a row lock (SELECT ... FOR UPDATE) so concurrent
    approvals/activations of the same session serialize."""
    return db.get(LearningSession, session_id, with_for_update=lock)


def list_sessions(db: Session, *, source_key: str | None, status: str | None, limit: int, offset: int):
    stmt = select(LearningSession)
    count = select(func.count()).select_from(LearningSession)
    if source_key:
        stmt, count = stmt.where(LearningSession.source_key == source_key), count.where(LearningSession.source_key == source_key)
    if status:
        stmt, count = stmt.where(LearningSession.status == status), count.where(LearningSession.status == status)
    total = db.execute(count).scalar_one()
    stmt = stmt.order_by(LearningSession.created_at.desc(), LearningSession.id.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all()), total


def open_session_for_trigger(db: Session, event_id: str) -> LearningSession | None:
    stmt = select(LearningSession).where(
        LearningSession.trigger_event_id == event_id, LearningSession.status.in_(OPEN_STATES)
    )
    return db.execute(stmt).scalars().first()


def events_with_signature(db: Session, adapter_id: str, signature: str) -> list[Event]:
    """Candidate events of this source sharing the given field-name signature."""
    stmt = (
        select(Event)
        .where(Event.adapter_id == adapter_id,
               Event.structural_fingerprint["signature"].astext == signature)
        .order_by(Event.received_at.desc())
        .limit(CANDIDATE_POOL)
    )
    return list(db.execute(stmt).scalars().all())


def processed_events(db: Session, adapter_id: str) -> list[Event]:
    """Recently, successfully processed events of this source (the pool for
    regression-safety evidence of previously accepted structures)."""
    stmt = (
        select(Event)
        .where(Event.adapter_id == adapter_id,
               Event.status.in_([EventStatus.SUCCESS.value, EventStatus.PARTIAL.value]))
        .order_by(Event.received_at.desc())
        .limit(CANDIDATE_POOL)
    )
    return list(db.execute(stmt).scalars().all())
