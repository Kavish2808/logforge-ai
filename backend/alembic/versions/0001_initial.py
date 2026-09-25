"""initial schema: events table

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("raw_event", sa.Text(), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("format_detected", sa.String(length=32), nullable=False),
        sa.Column("vendor", sa.String(length=128), nullable=True),
        sa.Column("product", sa.String(length=128), nullable=True),
        sa.Column("product_version", sa.String(length=64), nullable=True),
        sa.Column("adapter_id", sa.String(length=128), nullable=True),
        sa.Column("adapter_version", sa.String(length=32), nullable=True),
        sa.Column("ocsf_class_uid", sa.Integer(), nullable=True),
        sa.Column("ocsf_class_name", sa.String(length=128), nullable=True),
        sa.Column("ocsf_category_uid", sa.Integer(), nullable=True),
        sa.Column("ocsf_category_name", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("event_action", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("severity_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("network", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("user", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("process", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_event", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("processing_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("structural_fingerprint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_index("ix_events_raw_hash", "events", ["raw_hash"])
    op.create_index("ix_events_vendor", "events", ["vendor"])
    op.create_index("ix_events_status", "events", ["status"])
    op.create_index("ix_events_vendor_status", "events", ["vendor", "status"])
    op.create_index("ix_events_received_at", "events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_events_received_at", table_name="events")
    op.drop_index("ix_events_vendor_status", table_name="events")
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_vendor", table_name="events")
    op.drop_index("ix_events_raw_hash", table_name="events")
    op.drop_table("events")
