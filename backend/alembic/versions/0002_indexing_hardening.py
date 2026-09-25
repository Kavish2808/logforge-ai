"""indexing hardening: adapter_id lookups, drop redundant vendor index

The standalone ix_events_vendor index is redundant once
ix_events_vendor_status exists (Postgres can use a composite index's
leading column for vendor-only queries), so it is dropped here in favor
of adding an index that is actually missing: adapter_id, which the API
now supports filtering on.

Revision ID: 0002_indexing_hardening
Revises: 0001_initial
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_indexing_hardening"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_events_vendor", table_name="events")
    op.create_index("ix_events_adapter_id", "events", ["adapter_id"])


def downgrade() -> None:
    op.drop_index("ix_events_adapter_id", table_name="events")
    op.create_index("ix_events_vendor", "events", ["vendor"])
