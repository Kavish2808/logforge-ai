"""Phase 8 persistence: advanced drift findings, golden baselines, shadow
validation, replay with event revisions, compact lineage, drift correlation
and benchmark runs. All tables are additive (migration 0008); no Phase 0-7
table or column is altered."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# --- golden / current dual baseline --------------------------------------------------------------

GOLDEN_ACTIVE = "ACTIVE"
GOLDEN_SUPERSEDED = "SUPERSEDED"
GOLDEN_RETIRED = "RETIRED"  # explicitly withdrawn by a SOC_ADMIN; kept for audit


class GoldenBaseline(Base):
    """Immutable trusted structural + statistical reference for a source.
    Created only by an explicit SOC_ADMIN action; a replacement is a new
    version, the previous one is kept (SUPERSEDED)."""

    __tablename__ = "golden_baselines"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    statistical_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    derived_from_baseline_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_golden_baselines_source_version", "source_key", "version", unique=True),
        Index("uq_golden_baselines_one_active", "source_key", unique=True, postgresql_where=text("status = 'ACTIVE'")),
    )


class BaselineComparison(Base):
    """Evidence for one baseline-changing decision: NEW vs CURRENT vs GOLDEN."""

    __tablename__ = "baseline_comparisons"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the natural key
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_vs_current: Mapped[dict] = mapped_column(JSONB, nullable=False)
    new_vs_golden: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_vs_golden: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    steps_since_golden: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poisoning_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # ALLOWED | ELEVATED | BLOCKED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_baseline_comparisons_source_created", "source_key", "created_at"),)


# --- statistical / semantic drift + correlation -------------------------------------------------------

LAYER_STATISTICAL = "STATISTICAL"
LAYER_SEMANTIC = "SEMANTIC"


class DriftFinding(Base):
    __tablename__ = "drift_findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(source|field|metric|window_end|layer)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    deviation: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_n: Mapped[int] = mapped_column(Integer, nullable=False)
    current_n: Mapped[int] = mapped_column(Integer, nullable=False)
    quality: Mapped[str] = mapped_column(String(8), nullable=False)  # HIGH | MEDIUM | LOW
    advisory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_finding_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_drift_findings_source_created", "source_key", "created_at"),
        Index("ix_drift_findings_layer_status", "layer", "status"),
    )


class DriftCorrelation(Base):
    __tablename__ = "drift_correlations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(window + sorted members)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_keys: Mapped[list] = mapped_column(JSONB, nullable=False)
    vendors: Mapped[list] = mapped_column(JSONB, nullable=False)
    drift_types: Mapped[list] = mapped_column(JSONB, nullable=False)
    fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    member_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    score_components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN | REVIEWED | DISMISSED
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_drift_correlations_status_created", "status", "created_at"),)


# --- shadow validation ------------------------------------------------------------------------------

SHADOW_RUNNING = "RUNNING"
SHADOW_PASSED = "PASSED"
SHADOW_REVIEW_REQUIRED = "REVIEW_REQUIRED"
SHADOW_BLOCKED = "BLOCKED"


class ShadowRun(Base):
    """Stratified old-vs-new comparison for one learning proposal. The learning
    session is a soft reference (Demo reset deletes learning sessions)."""

    __tablename__ = "shadow_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    learning_session_id: Mapped[str] = mapped_column(String(26), nullable=False)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    old_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strata: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff: Mapped[list] = mapped_column(JSONB, nullable=False)  # capped per-event diffs
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # PASS | REGRESSION | CRITICAL
    breaker_tripped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False)
    latency: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_shadow_runs_session_proposal", "learning_session_id", "proposal_version"),)


# --- revision-aware replay ------------------------------------------------------------------------------

REPLAY_PENDING = "PENDING"
REPLAY_PENDING_APPROVAL = "PENDING_APPROVAL"  # > 10,000 events: a second authorized actor must start it
REPLAY_RUNNING = "RUNNING"
REPLAY_PAUSED = "PAUSED"
REPLAY_COMPLETED = "COMPLETED"
REPLAY_FAILED = "FAILED"
REPLAY_CANCELLED = "CANCELLED"


class ReplayJob(Base):
    __tablename__ = "replay_jobs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # ROLLBACK | MANUAL
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_version: Mapped[str] = mapped_column(String(32), nullable=False)
    to_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rate_per_sec: Mapped[float] = mapped_column(Float, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_replay_jobs_status", "status"),
        Index("ix_replay_jobs_adapter_version", "adapter_id", "from_version"),
    )


class EventRevision(Base):
    """One processing revision of an event. event_id is the stable evidence
    identity; revisions are append-only and never pruned."""

    __tablename__ = "event_revisions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(26), ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("event_revisions.id", ondelete="SET NULL"), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # ORIGINAL | REPLAY | REPROCESS
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replay_job_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("replay_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("uq_event_revisions_event_revision", "event_id", "revision_no", unique=True),
        Index("uq_event_revisions_event_job", "event_id", "replay_job_id", unique=True,
              postgresql_where=text("replay_job_id IS NOT NULL")),
        Index("uq_event_revisions_one_current", "event_id", unique=True, postgresql_where=text("is_current")),
    )


# --- compact lineage ------------------------------------------------------------------------------------


class EventLineageCompact(Base):
    __tablename__ = "event_lineage_compact"

    event_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    template_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stage_mask: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exception_mask: Mapped[int] = mapped_column(Integer, nullable=False)
    is_exception: Mapped[bool] = mapped_column(Boolean, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_event_lineage_compact_exceptions", "exception_mask", postgresql_where=text("is_exception")),
    )


# --- benchmarks -----------------------------------------------------------------------------------------


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    workload_size: Mapped[int] = mapped_column(Integer, nullable=False)
    variant: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    environment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_benchmark_runs_scenario_created", "scenario", "created_at"),)
