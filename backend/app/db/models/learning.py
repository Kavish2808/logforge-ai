"""Phase 6 learning sessions: an auditable attempt to learn a human-approved
structural evolution of an onboarded adapter as a new adapter version.

Adapter versions themselves stay in `onboarded_adapters` (Phase 3's
versioning model); a learning session references its source version and,
once activated, the version it created. Evidence references stored events
by id + SHA-256 (no event copies).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROPOSED = "PROPOSED"
VALIDATED = "VALIDATED"
NEEDS_REVIEW = "NEEDS_REVIEW"
FAILED = "FAILED"
APPROVED = "APPROVED"
ACTIVE = "ACTIVE"
REJECTED = "REJECTED"
ROLLED_BACK = "ROLLED_BACK"
NO_CHANGE_REQUIRED = "NO_CHANGE_REQUIRED"

OPEN_STATES = (PROPOSED, VALIDATED, NEEDS_REVIEW, FAILED, APPROVED)


class LearningSession(Base):
    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_adapter_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_adapter_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("onboarded_adapters.id", ondelete="RESTRICT"), nullable=False
    )
    trigger_event_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    trigger_raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    drift: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Phase 5 drift record snapshot
    old_fingerprint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)  # event ids + raw hashes
    learning_modes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    proposal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # LearningDelta
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposal_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assistant: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # proposed AdapterMapping
    mapping_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    target_adapter_row_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("onboarded_adapters.id", ondelete="RESTRICT"), nullable=True
    )
    decisions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # At most one open learning session per drift trigger event.
        Index(
            "uq_learning_sessions_open_trigger",
            "trigger_event_id",
            unique=True,
            postgresql_where=text("status IN ('PROPOSED','VALIDATED','NEEDS_REVIEW','FAILED','APPROVED')"),
        ),
    )
