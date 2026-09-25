"""The universal event record.

This single table holds the complete lifecycle of a log event: the raw
input (immutable, always preserved), the OCSF-aligned normalized output,
everything that didn't map cleanly (extensions), and pipeline metadata
needed for traceability and debugging (processing_metadata, warnings,
error_message, structural_fingerprint).
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Event(Base):
    __tablename__ = "events"

    # --- identity & traceability ---
    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    raw_event: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # --- timing ---
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- source identification ---
    format_detected: Mapped[str] = mapped_column(String(32), nullable=False)
    # No standalone index on vendor: ix_events_vendor_status below already
    # serves vendor-only queries via its leading column, so a second index
    # would just add write overhead for no query benefit.
    vendor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- adapter (vendor mapping) used ---
    adapter_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    adapter_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- OCSF classification ---
    ocsf_class_uid: Mapped[int | None] = mapped_column(nullable=True)
    ocsf_class_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ocsf_category_uid: Mapped[int | None] = mapped_column(nullable=True)
    ocsf_category_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Text, not VARCHAR(128): event_action is frequently mapped straight from a
    # free-text log message (e.g. Cisco ASA's %ASA-... message body), which can
    # be arbitrarily long.
    event_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    severity_id: Mapped[int | None] = mapped_column(nullable=True)

    # --- lifecycle status ---
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # --- structured, semi-structured data ---
    network: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    user: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    process: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extensions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    normalized_event: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- pipeline bookkeeping ---
    processing_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    structural_fingerprint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_events_vendor_status", "vendor", "status"),
        Index("ix_events_received_at", "received_at"),
    )
