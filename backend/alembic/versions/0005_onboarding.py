"""adaptive onboarding: onboarding_sessions + onboarded_adapters

Additive only — no existing table is altered. onboarded_adapters holds
immutable, versioned, human-approved adapters; a partial unique index
guarantees at most one ACTIVE version per adapter_id.

Revision ID: 0005_onboarding
Revises: 0004_baseline_history
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_onboarding"
down_revision: Union[str, None] = "0004_baseline_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "onboarding_sessions",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("samples", JSONB, nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("analysis", JSONB, nullable=False),
        sa.Column("proposal", JSONB, nullable=True),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("proposal_source", sa.String(length=64), nullable=True),
        sa.Column("suggestion_error", JSONB, nullable=True),
        sa.Column("validation", JSONB, nullable=True),
        sa.Column("decisions", JSONB, nullable=False),
        sa.Column("adapter_id", sa.String(length=128), nullable=True),
        sa.Column("adapter_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_onboarding_sessions_status", "onboarding_sessions", ["status"])

    op.create_table(
        "onboarded_adapters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("adapter_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mapping", JSONB, nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("validation_summary", JSONB, nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("adapter_id", "version", name="uq_onboarded_adapters_adapter_version"),
    )
    op.create_index("ix_onboarded_adapters_adapter_id", "onboarded_adapters", ["adapter_id"])
    op.create_index(
        "uq_onboarded_adapters_one_active",
        "onboarded_adapters",
        ["adapter_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_onboarded_adapters_one_active", table_name="onboarded_adapters")
    op.drop_index("ix_onboarded_adapters_adapter_id", table_name="onboarded_adapters")
    op.drop_table("onboarded_adapters")
    op.drop_index("ix_onboarding_sessions_status", table_name="onboarding_sessions")
    op.drop_table("onboarding_sessions")
