"""drift detection: source_baseline_history table

Lightweight structural evolution per source (one row per baseline
version). Additive only — neither `events` nor `source_baselines` is
altered. Baselines that already exist get one HISTORY_STARTED row so
their history is never empty.

Revision ID: 0004_baseline_history
Revises: 0003_drift_baselines
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_baseline_history"
down_revision: Union[str, None] = "0003_drift_baselines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_baseline_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=26), nullable=True),
        sa.Column("signature", sa.String(length=64), nullable=True),
        sa.Column("field_count", sa.Integer(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_source_baseline_history_source_key", "source_baseline_history", ["source_key"])
    op.execute(
        """
        INSERT INTO source_baseline_history (source_key, version, action, event_id, signature, field_count, note)
        SELECT source_key, version, 'HISTORY_STARTED', created_from_event_id,
               fingerprint->>'signature', (fingerprint->>'field_count')::int,
               'History tracking began at this version (baseline predates history).'
        FROM source_baselines
        """
    )


def downgrade() -> None:
    op.drop_index("ix_source_baseline_history_source_key", table_name="source_baseline_history")
    op.drop_table("source_baseline_history")
