"""continuous adaptive learning: learning_sessions

Additive only — no existing table is altered. Learned adapter versions are
stored in Phase 3's onboarded_adapters; learning_sessions references them
by foreign key.

Revision ID: 0006_learning_sessions
Revises: 0005_onboarding
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_learning_sessions"
down_revision: Union[str, None] = "0005_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("source_adapter_id", sa.String(length=128), nullable=False),
        sa.Column("source_adapter_version", sa.Integer(), nullable=False),
        sa.Column("source_adapter_row_id", sa.Integer(),
                  sa.ForeignKey("onboarded_adapters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("trigger_event_id", sa.String(length=26), nullable=False),
        sa.Column("trigger_raw_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("drift", JSONB, nullable=False),
        sa.Column("old_fingerprint", JSONB, nullable=True),
        sa.Column("new_fingerprint", JSONB, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("learning_modes", JSONB, nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("risk_reasons", JSONB, nullable=False),
        sa.Column("proposal", JSONB, nullable=True),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("proposal_source", sa.String(length=64), nullable=True),
        sa.Column("assistant", JSONB, nullable=True),
        sa.Column("candidate", JSONB, nullable=True),
        sa.Column("mapping_diff", JSONB, nullable=True),
        sa.Column("target_version", sa.Integer(), nullable=True),
        sa.Column("validation", JSONB, nullable=True),
        sa.Column("target_adapter_row_id", sa.Integer(),
                  sa.ForeignKey("onboarded_adapters.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("decisions", JSONB, nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_learning_sessions_source_key", "learning_sessions", ["source_key"])
    op.create_index("ix_learning_sessions_status", "learning_sessions", ["status"])
    op.create_index("ix_learning_sessions_trigger_event_id", "learning_sessions", ["trigger_event_id"])
    op.create_index(
        "uq_learning_sessions_open_trigger",
        "learning_sessions",
        ["trigger_event_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PROPOSED','VALIDATED','NEEDS_REVIEW','FAILED','APPROVED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_learning_sessions_open_trigger", table_name="learning_sessions")
    op.drop_index("ix_learning_sessions_trigger_event_id", table_name="learning_sessions")
    op.drop_index("ix_learning_sessions_status", table_name="learning_sessions")
    op.drop_index("ix_learning_sessions_source_key", table_name="learning_sessions")
    op.drop_table("learning_sessions")
