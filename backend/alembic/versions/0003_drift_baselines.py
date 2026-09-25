"""drift detection: source_baselines table

Additive only — the events table is not changed. Drift results are
stored per event under processing_metadata.drift (existing JSONB column),
and UNDER_REVIEW fits the existing status VARCHAR(16).

Revision ID: 0003_drift_baselines
Revises: 0002_indexing_hardening
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_drift_baselines"
down_revision: Union[str, None] = "0002_indexing_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_baselines",
        sa.Column("source_key", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("adapter_id", sa.String(length=128), nullable=False),
        sa.Column("adapter_version", sa.String(length=32), nullable=True),
        sa.Column("format_detected", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("accepted_variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_from_event_id", sa.String(length=26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    # Events already marked UNDER_REVIEW keep that status (plain string
    # column); their processing_metadata.drift record is left untouched.
    op.drop_table("source_baselines")
