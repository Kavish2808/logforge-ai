"""Data access for onboarding sessions and versioned onboarded adapters.
Nothing here commits; the service owns transaction boundaries."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.onboarding import ADAPTER_ACTIVE, OnboardedAdapter, OnboardingSession


def add_session(db: Session, session: OnboardingSession) -> OnboardingSession:
    db.add(session)
    return session


def get_session(db: Session, session_id: str) -> OnboardingSession | None:
    return db.get(OnboardingSession, session_id)


def list_sessions(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[OnboardingSession], int]:
    total = db.execute(select(func.count()).select_from(OnboardingSession)).scalar_one()
    stmt = select(OnboardingSession).order_by(OnboardingSession.created_at.desc(), OnboardingSession.id.desc())
    return list(db.execute(stmt.limit(limit).offset(offset)).scalars().all()), total


def active_adapters(db: Session) -> list[OnboardedAdapter]:
    """ACTIVE versions in approval order (older approvals are tried first)."""
    stmt = select(OnboardedAdapter).where(OnboardedAdapter.status == ADAPTER_ACTIVE).order_by(OnboardedAdapter.id)
    return list(db.execute(stmt).scalars().all())


def active_adapter_keys(db: Session) -> tuple[tuple[int, str, int], ...]:
    stmt = (
        select(OnboardedAdapter.id, OnboardedAdapter.adapter_id, OnboardedAdapter.version)
        .where(OnboardedAdapter.status == ADAPTER_ACTIVE)
        .order_by(OnboardedAdapter.id)
    )
    return tuple((row[0], row[1], row[2]) for row in db.execute(stmt).all())


def adapter_versions(db: Session, adapter_id: str) -> list[OnboardedAdapter]:
    stmt = select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == adapter_id).order_by(OnboardedAdapter.version)
    return list(db.execute(stmt).scalars().all())


def adapter_ids(db: Session) -> list[str]:
    stmt = select(OnboardedAdapter.adapter_id).distinct().order_by(OnboardedAdapter.adapter_id)
    return list(db.execute(stmt).scalars().all())
