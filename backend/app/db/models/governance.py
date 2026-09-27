"""Phase 7 governance persistence: users/roles, auth tokens, the hash-chained
audit log, review SLAs, runtime governance settings, the confidence evidence
ledger, alerts and export activity. All tables are additive."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # "pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>" — never a plaintext password.
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 of the bearer token
    user_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Append-only, hash-chained audit record. current_hash = SHA-256 over the
    canonical JSON of every other column plus previous_hash."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    audit_id: Mapped[str] = mapped_column(String(26), nullable=False, unique=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # SUCCESS | DENIED | FAILED
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_audit_log_object", "object_type", "object_id"),)


SLA_PENDING = "PENDING"
SLA_DUE_SOON = "DUE_SOON"
SLA_OVERDUE = "OVERDUE"
SLA_ESCALATED = "ESCALATED"
SLA_RESOLVED = "RESOLVED"
SLA_OPEN_STATES = (SLA_PENDING, SLA_DUE_SOON, SLA_OVERDUE, SLA_ESCALATED)


class ReviewSla(Base):
    """Durable review deadline for one item awaiting a human decision."""

    __tablename__ = "review_slas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)  # DRIFT_EVENT | ONBOARDING_SESSION | LEARNING_SESSION
    item_id: Mapped[str] = mapped_column(String(26), nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_hours: Mapped[float] = mapped_column(Float, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    escalation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_review_slas_item", "item_type", "item_id", unique=True),)


class GovernanceSetting(Base):
    """Runtime-tunable governance configuration (changed only by SOC_ADMIN, audited)."""

    __tablename__ = "governance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ConfidenceLedgerEntry(Base):
    """Evidence behind one parser suggestion (one proposal version)."""

    __tablename__ = "confidence_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ONBOARDING | LEARNING
    subject_id: Mapped[str] = mapped_column(String(26), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suggestion_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_confidence_ledger_subject", "subject_type", "subject_id", "proposal_version", unique=True),
    )


ALERT_OPEN = "OPEN"
ALERT_ACKNOWLEDGED = "ACKNOWLEDGED"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(256), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deliveries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # At most one OPEN alert per dedup key; repeats bump `occurrences`.
        Index("uq_alerts_open_dedup", "dedup_key", unique=True, postgresql_where=text("status = 'OPEN'")),
    )


class ExportLog(Base):
    __tablename__ = "export_log"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    max_events: Mapped[int] = mapped_column(Integer, nullable=False)
    include_raw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # STARTED | COMPLETED | FAILED
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_more: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
