"""phase 7 trust / integration / governance layer

Additive only — no existing table or column is altered. Adds:
- event_extension_overflow, overflow_signatures (adaptive extension spill)
- event_raw_storage (cold raw vault metadata)
- evidence_batches, evidence_batch_members (Merkle evidence chain)
- users, auth_tokens (lightweight RBAC)
- audit_log (hash-chained audit)
- review_slas, governance_settings, confidence_ledger, alerts, export_log

Revision ID: 0007_phase7_trust_layer
Revises: 0006_learning_sessions
Create Date: 2026-09-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase7_trust_layer"
down_revision: Union[str, None] = "0006_learning_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TZ = sa.DateTime(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    # --- extension spill ---------------------------------------------------------------
    op.create_table(
        "event_extension_overflow",
        sa.Column("event_id", sa.String(26), sa.ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("adapter_id", sa.String(128), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("key_order", JSONB, nullable=False),
        sa.Column("field_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("key_signature", sa.String(64), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_event_extension_overflow_adapter_id", "event_extension_overflow", ["adapter_id"])
    op.create_index("ix_event_extension_overflow_key_signature", "event_extension_overflow", ["key_signature"])

    op.create_table(
        "overflow_signatures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("adapter_id", sa.String(128), nullable=False),
        sa.Column("key_signature", sa.String(64), nullable=False),
        sa.Column("keys", JSONB, nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("sample_event_ids", JSONB, nullable=False),
        sa.Column("first_seen", TZ, server_default=NOW, nullable=False),
        sa.Column("last_seen", TZ, server_default=NOW, nullable=False),
        sa.Column("onboarding_session_id", sa.String(26), nullable=True),
    )
    op.create_index("uq_overflow_signatures_adapter_sig", "overflow_signatures", ["adapter_id", "key_signature"], unique=True)

    # --- cold raw vault ------------------------------------------------------------------
    op.create_table(
        "event_raw_storage",
        sa.Column("event_id", sa.String(26), sa.ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("backend", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(256), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("encoding", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("stored_at", TZ, nullable=True),
        sa.Column("verified_at", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_event_raw_storage_status", "event_raw_storage", ["status"])

    # --- Merkle evidence chain -------------------------------------------------------------
    op.create_table(
        "evidence_batches",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("batch_id", sa.String(26), nullable=False, unique=True),
        sa.Column("start_time", TZ, nullable=False),
        sa.Column("end_time", TZ, nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("prev_chain_hash", sa.String(64), nullable=False),
        sa.Column("chain_hash", sa.String(64), nullable=False),
        sa.Column("leaf_algorithm", sa.String(64), nullable=False),
        sa.Column("anchor_backend", sa.String(32), nullable=False),
        sa.Column("anchor_ref", sa.String(256), nullable=True),
        sa.Column("anchored_at", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_table(
        "evidence_batch_members",
        sa.Column("event_id", sa.String(26), primary_key=True),
        sa.Column("batch_seq", sa.BigInteger(), sa.ForeignKey("evidence_batches.seq", ondelete="RESTRICT"), nullable=False),
        sa.Column("leaf_index", sa.Integer(), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("leaf_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_evidence_batch_members_batch_seq", "evidence_batch_members", ["batch_seq"])

    # --- RBAC ------------------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
        sa.Column("updated_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_table(
        "auth_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(26), nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    # --- audit -----------------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("audit_id", sa.String(26), nullable=False, unique=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("authenticated", sa.Boolean(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("timestamp", TZ, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("evidence_ref", sa.String(256), nullable=True),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("current_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_object", "audit_log", ["object_type", "object_id"])

    # --- review SLA / settings / confidence / alerts / export -----------------------------------
    op.create_table(
        "review_slas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_id", sa.String(26), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("opened_at", TZ, nullable=False),
        sa.Column("sla_hours", sa.Float(), nullable=False),
        sa.Column("due_at", TZ, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("escalation_count", sa.Integer(), nullable=False),
        sa.Column("last_escalated_at", TZ, nullable=True),
        sa.Column("resolved_at", TZ, nullable=True),
        sa.Column("resolution", sa.String(64), nullable=True),
        sa.Column("fallback", sa.Text(), nullable=True),
        sa.Column("updated_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_review_slas_status", "review_slas", ["status"])
    op.create_index("uq_review_slas_item", "review_slas", ["item_type", "item_id"], unique=True)

    op.create_table(
        "governance_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("updated_at", TZ, server_default=NOW, nullable=False),
    )

    op.create_table(
        "confidence_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(26), nullable=False),
        sa.Column("proposal_version", sa.Integer(), nullable=False),
        sa.Column("proposal_source", sa.String(64), nullable=True),
        sa.Column("adapter_id", sa.String(128), nullable=True),
        sa.Column("suggestion_confidence", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("uq_confidence_ledger_subject", "confidence_ledger",
                    ["subject_type", "subject_id", "proposal_version"], unique=True)

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=True),
        sa.Column("object_id", sa.String(128), nullable=True),
        sa.Column("dedup_key", sa.String(256), nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("deliveries", JSONB, nullable=False),
        sa.Column("acknowledged_by", sa.String(128), nullable=True),
        sa.Column("acknowledged_at", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=NOW, nullable=False),
        sa.Column("last_seen_at", TZ, server_default=NOW, nullable=False),
    )
    op.create_index("ix_alerts_kind", "alerts", ["kind"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("uq_alerts_open_dedup", "alerts", ["dedup_key"], unique=True,
                    postgresql_where=sa.text("status = 'OPEN'"))

    op.create_table(
        "export_log",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("filters", JSONB, nullable=False),
        sa.Column("max_events", sa.Integer(), nullable=False),
        sa.Column("include_raw", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("has_more", sa.Boolean(), nullable=True),
        sa.Column("started_at", TZ, server_default=NOW, nullable=False),
        sa.Column("completed_at", TZ, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("export_log")
    op.drop_index("uq_alerts_open_dedup", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_kind", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("uq_confidence_ledger_subject", table_name="confidence_ledger")
    op.drop_table("confidence_ledger")
    op.drop_table("governance_settings")
    op.drop_index("uq_review_slas_item", table_name="review_slas")
    op.drop_index("ix_review_slas_status", table_name="review_slas")
    op.drop_table("review_slas")
    op.drop_index("ix_audit_log_object", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_table("users")
    op.drop_index("ix_evidence_batch_members_batch_seq", table_name="evidence_batch_members")
    op.drop_table("evidence_batch_members")
    op.drop_table("evidence_batches")
    op.drop_index("ix_event_raw_storage_status", table_name="event_raw_storage")
    op.drop_table("event_raw_storage")
    op.drop_index("uq_overflow_signatures_adapter_sig", table_name="overflow_signatures")
    op.drop_table("overflow_signatures")
    op.drop_index("ix_event_extension_overflow_key_signature", table_name="event_extension_overflow")
    op.drop_index("ix_event_extension_overflow_adapter_id", table_name="event_extension_overflow")
    op.drop_table("event_extension_overflow")
