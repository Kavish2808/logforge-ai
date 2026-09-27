"""Phase 7 evidence storage: extension overflow, cold raw vault metadata and
the Merkle evidence chain.

All tables are additive. Nothing here replaces a Phase 0-6 column: the event
row keeps its raw payload, SHA-256 and (inline) extensions; these tables hold
what does not fit inline, where the cold copy of the raw bytes lives, and
which sealed batch an event hash belongs to.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INLINE = "INLINE"
SPILLED = "SPILLED"


class EventExtensionOverflow(Base):
    """Extensions beyond the inline budget, stored losslessly (1:1 with a
    spilled event). inline ∪ overflow == the full extension set, disjoint."""

    __tablename__ = "event_extension_overflow"

    event_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    key_order: Mapped[list] = mapped_column(JSONB, nullable=False)  # original key order of the overflow
    field_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)  # canonical JSON bytes of payload
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    key_signature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OverflowSignature(Base):
    """Repeated overflow key structures per adapter: onboarding evidence."""

    __tablename__ = "overflow_signatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    key_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    keys: Mapped[list] = mapped_column(JSONB, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_event_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # bounded
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    onboarding_session_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    __table_args__ = (Index("uq_overflow_signatures_adapter_sig", "adapter_id", "key_signature", unique=True),)


RAW_STORED = "STORED"
RAW_FAILED = "FAILED"


class EventRawStorage(Base):
    """Where the cold copy of an event's raw bytes lives, and its integrity."""

    __tablename__ = "event_raw_storage"

    event_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # HOT_AND_COLD | HOT_ONLY
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # STORED | FAILED
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    encoding: Mapped[str] = mapped_column(String(16), nullable=False, default="utf-8")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceBatch(Base):
    """A sealed Merkle batch of event hashes, chained to its predecessor."""

    __tablename__ = "evidence_batches"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    batch_id: Mapped[str] = mapped_column(String(26), nullable=False, unique=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    root_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    leaf_algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    anchored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceBatchMember(Base):
    """Membership of an event hash in a sealed batch. Soft reference to the
    event (no FK): a later event deletion must not erase sealed evidence."""

    __tablename__ = "evidence_batch_members"

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    batch_seq: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_batches.seq", ondelete="RESTRICT"), nullable=False, index=True
    )
    leaf_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    leaf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
