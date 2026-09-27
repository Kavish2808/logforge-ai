"""phase 8 scale, advanced adaptation and resilience

Additive only — no existing table or column is altered. Adds:
- golden_baselines, baseline_comparisons   (golden + current dual baseline)
- drift_findings, drift_correlations        (statistical/semantic drift, cross-vendor correlation)
- shadow_runs                               (stratified shadow validation)
- replay_jobs, event_revisions              (revision-aware replay)
- event_lineage_compact                     (compact lineage)
- benchmark_runs                            (measured benchmark results)

Revision ID: 0008_phase8_resilience
Revises: 0007_phase7_trust_layer
Create Date: 2026-09-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_phase8_resilience"
down_revision: Union[str, None] = "0007_phase7_trust_layer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TZ = sa.DateTime(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "golden_baselines",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", JSONB, nullable=False),
        sa.Column("statistical_profile", JSONB, nullable=False),
        sa.Column("derived_from_baseline_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("approved_role", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("uq_golden_baselines_source_version", "golden_baselines", ["source_key", "version"], unique=True)
    op.create_index("uq_golden_baselines_one_active", "golden_baselines", ["source_key"], unique=True,
                    postgresql_where=sa.text("status = 'ACTIVE'"))

    op.create_table(
        "baseline_comparisons",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=True),
        sa.Column("new_vs_current", JSONB, nullable=False),
        sa.Column("new_vs_golden", JSONB, nullable=True),
        sa.Column("current_vs_golden", JSONB, nullable=True),
        sa.Column("steps_since_golden", sa.Integer(), nullable=True),
        sa.Column("poisoning_risk", sa.Boolean(), nullable=False),
        sa.Column("risk_reasons", JSONB, nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_baseline_comparisons_source_created", "baseline_comparisons", ["source_key", "created_at"])

    op.create_table(
        "drift_findings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("field", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("baseline_start", TZ, nullable=False),
        sa.Column("baseline_end", TZ, nullable=False),
        sa.Column("current_start", TZ, nullable=False),
        sa.Column("current_end", TZ, nullable=False),
        sa.Column("baseline_value", JSONB, nullable=False),
        sa.Column("current_value", JSONB, nullable=False),
        sa.Column("deviation", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("baseline_n", sa.Integer(), nullable=False),
        sa.Column("current_n", sa.Integer(), nullable=False),
        sa.Column("quality", sa.String(8), nullable=False),
        sa.Column("advisory", sa.Boolean(), nullable=False),
        sa.Column("parent_finding_id", sa.String(64), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("acknowledged_by", sa.String(128), nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_drift_findings_source_created", "drift_findings", ["source_key", "created_at"])
    op.create_index("ix_drift_findings_layer_status", "drift_findings", ["layer", "status"])

    op.create_table(
        "drift_correlations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("window_start", TZ, nullable=False),
        sa.Column("window_end", TZ, nullable=False),
        sa.Column("source_keys", JSONB, nullable=False),
        sa.Column("vendors", JSONB, nullable=False),
        sa.Column("drift_types", JSONB, nullable=False),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("member_refs", JSONB, nullable=False),
        sa.Column("score_components", JSONB, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_drift_correlations_status_created", "drift_correlations", ["status", "created_at"])

    op.create_table(
        "shadow_runs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("learning_session_id", sa.String(26), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("old_version", sa.String(32), nullable=True),
        sa.Column("new_version", sa.String(32), nullable=True),
        sa.Column("strata", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("diff", JSONB, nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("breaker_tripped", sa.Boolean(), nullable=False),
        sa.Column("reasons", JSONB, nullable=False),
        sa.Column("thresholds", JSONB, nullable=False),
        sa.Column("latency", JSONB, nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_shadow_runs_session_proposal", "shadow_runs", ["learning_session_id", "proposal_version"])

    op.create_table(
        "replay_jobs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("adapter_id", sa.String(128), nullable=False),
        sa.Column("from_version", sa.String(32), nullable=False),
        sa.Column("to_version", sa.String(32), nullable=True),
        sa.Column("window_start", TZ, nullable=True),
        sa.Column("window_end", TZ, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("cursor", JSONB, nullable=True),
        sa.Column("rate_per_sec", sa.Float(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
        sa.Column("updated_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_replay_jobs_status", "replay_jobs", ["status"])
    op.create_index("ix_replay_jobs_adapter_version", "replay_jobs", ["adapter_id", "from_version"])

    op.create_table(
        "event_revisions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("event_id", sa.String(26), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(26), sa.ForeignKey("event_revisions.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("adapter_id", sa.String(128), nullable=True),
        sa.Column("adapter_version", sa.String(32), nullable=True),
        sa.Column("replay_job_id", sa.String(26), sa.ForeignKey("replay_jobs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("uq_event_revisions_event_revision", "event_revisions", ["event_id", "revision_no"], unique=True)
    op.create_index("uq_event_revisions_event_job", "event_revisions", ["event_id", "replay_job_id"], unique=True,
                    postgresql_where=sa.text("replay_job_id IS NOT NULL"))
    op.create_index("uq_event_revisions_one_current", "event_revisions", ["event_id"], unique=True,
                    postgresql_where=sa.text("is_current"))

    op.create_table(
        "event_lineage_compact",
        sa.Column("event_id", sa.String(26), sa.ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("template_version", sa.SmallInteger(), nullable=False),
        sa.Column("stage_mask", sa.BigInteger(), nullable=False),
        sa.Column("exception_mask", sa.Integer(), nullable=False),
        sa.Column("is_exception", sa.Boolean(), nullable=False),
        sa.Column("computed_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_event_lineage_compact_exceptions", "event_lineage_compact", ["exception_mask"],
                    postgresql_where=sa.text("is_exception"))

    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("scenario", sa.String(64), nullable=False),
        sa.Column("workload_size", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(32), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("environment", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ, nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_benchmark_runs_scenario_created", "benchmark_runs", ["scenario", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_runs_scenario_created", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
    op.drop_index("ix_event_lineage_compact_exceptions", table_name="event_lineage_compact")
    op.drop_table("event_lineage_compact")
    op.drop_index("uq_event_revisions_one_current", table_name="event_revisions")
    op.drop_index("uq_event_revisions_event_job", table_name="event_revisions")
    op.drop_index("uq_event_revisions_event_revision", table_name="event_revisions")
    op.drop_table("event_revisions")
    op.drop_index("ix_replay_jobs_adapter_version", table_name="replay_jobs")
    op.drop_index("ix_replay_jobs_status", table_name="replay_jobs")
    op.drop_table("replay_jobs")
    op.drop_index("ix_shadow_runs_session_proposal", table_name="shadow_runs")
    op.drop_table("shadow_runs")
    op.drop_index("ix_drift_correlations_status_created", table_name="drift_correlations")
    op.drop_table("drift_correlations")
    op.drop_index("ix_drift_findings_layer_status", table_name="drift_findings")
    op.drop_index("ix_drift_findings_source_created", table_name="drift_findings")
    op.drop_table("drift_findings")
    op.drop_index("ix_baseline_comparisons_source_created", table_name="baseline_comparisons")
    op.drop_table("baseline_comparisons")
    op.drop_index("uq_golden_baselines_one_active", table_name="golden_baselines")
    op.drop_index("uq_golden_baselines_source_version", table_name="golden_baselines")
    op.drop_table("golden_baselines")
