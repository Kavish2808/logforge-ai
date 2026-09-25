"""Per-source structural baseline used by drift detection (Phase 5).

One row per known vendor source (source_key == the vendor adapter_id).
The first structure observed for a source is stored automatically with
origin="auto_bootstrap" — a provisional baseline so detection can start
without setup. It is only ever replaced, or extended with accepted
variants, through an explicit human review action (origin becomes
"human_review"); nothing in the ingestion path changes a baseline once
it exists.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ORIGIN_AUTO_BOOTSTRAP = "auto_bootstrap"
ORIGIN_HUMAN_REVIEW = "human_review"


class SourceBaseline(Base):
    __tablename__ = "source_baselines"

    source_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    format_detected: Mapped[str] = mapped_column(String(32), nullable=False)

    # Reference structural fingerprint (field_set, field_order, field_count,
    # signature, field_types) — same shape as events.structural_fingerprint.
    fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Human-accepted alternative structures:
    # [{"fingerprint": {...}, "accepted_from_event_id", "accepted_at", "note"}]
    accepted_variants: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    origin: Mapped[str] = mapped_column(String(32), nullable=False, default=ORIGIN_AUTO_BOOTSTRAP)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Soft reference (no FK): the baseline row is written in the same
    # transaction as, and before, the event that bootstrapped it.
    created_from_event_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


HISTORY_BASELINE_CREATED = "BASELINE_CREATED"
HISTORY_VARIANT_ADDED = "VARIANT_ADDED"
HISTORY_BASELINE_REPLACED = "BASELINE_REPLACED"
HISTORY_STARTED = "HISTORY_STARTED"  # backfilled for baselines that predate history tracking


class SourceBaselineHistory(Base):
    """Append-only structural evolution of a source: one row per baseline
    version (v1 created, v2 variant added, v3 replaced, ...). Stores the
    structural delta and a pointer to the triggering event — never a copy
    of the event itself."""

    __tablename__ = "source_baseline_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    field_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
