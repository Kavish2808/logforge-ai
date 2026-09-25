"""Adaptive unknown-vendor onboarding persistence.

- OnboardingSession: the samples (always preserved), the deterministic
  analysis, the current proposal, its sandbox validation, and every human
  decision. One session per onboarding attempt.
- OnboardedAdapter: an immutable, versioned, human-approved adapter. At most
  one version per adapter_id is ACTIVE (enforced by a partial unique index);
  the runtime registry is built from ACTIVE rows only.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SESSION_COLLECTED = "COLLECTED"
SESSION_SUGGESTION_FAILED = "SUGGESTION_FAILED"
SESSION_VALIDATED = "VALIDATED"
SESSION_APPROVED = "APPROVED"
SESSION_REJECTED = "REJECTED"

ADAPTER_ACTIVE = "ACTIVE"
ADAPTER_SUPERSEDED = "SUPERSEDED"
ADAPTER_ROLLED_BACK = "ROLLED_BACK"


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # [{"index", "raw", "raw_hash", "source_event_id"}] — never modified or dropped.
    samples: Mapped[list] = mapped_column(JSONB, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    proposal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposal_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggestion_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decisions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    adapter_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adapter_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OnboardedAdapter(Base):
    __tablename__ = "onboarded_adapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)  # AdapterMapping as JSON
    session_id: Mapped[str] = mapped_column(String(26), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("adapter_id", "version", name="uq_onboarded_adapters_adapter_version"),
        Index(
            "uq_onboarded_adapters_one_active",
            "adapter_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
