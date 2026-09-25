"""OCSF-aligned universal event schema.

This is the canonical shape every parsed/normalized log is converted into,
regardless of source format or vendor. Fields loosely follow OCSF (Open
Cybersecurity Schema Framework) top-level concepts (class/category,
type/action, severity, actor, network endpoints) while remaining generic
enough to hold partially-parsed or failed events.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNDER_REVIEW = "UNDER_REVIEW"


class FormatType(StrEnum):
    SYSLOG = "syslog"
    JSON = "json"
    CEF = "cef"
    UNKNOWN = "unknown"


class NetworkInfo(BaseModel):
    src_ip: str | None = None
    dst_ip: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    protocol: str | None = None
    direction: str | None = None


class UserInfo(BaseModel):
    name: str | None = None
    uid: str | None = None
    domain: str | None = None


class ProcessInfo(BaseModel):
    name: str | None = None
    pid: int | None = None
    path: str | None = None
    cmd_line: str | None = None


class ProcessingMetadata(BaseModel):
    parser: str | None = None
    pipeline_version: str = "0.1.0"
    duration_ms: float | None = None
    adapter_source: str | None = None  # "manual" | "llm_generated"


class StructuralFingerprint(BaseModel):
    field_set: list[str] = Field(default_factory=list)
    field_order: list[str] = Field(default_factory=list)
    field_count: int = 0
    signature: str | None = None


class UniversalEvent(BaseModel):
    """The complete OCSF-aligned universal event, as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    raw_event: str
    raw_hash: str

    received_at: datetime
    processed_at: datetime | None = None
    event_timestamp: datetime | None = None

    format_detected: FormatType
    vendor: str | None = None
    product: str | None = None
    product_version: str | None = None

    adapter_id: str | None = None
    adapter_version: str | None = None

    ocsf_class_uid: int | None = None
    ocsf_class_name: str | None = None
    ocsf_category_uid: int | None = None
    ocsf_category_name: str | None = None
    event_type: str | None = None
    event_action: str | None = None
    severity: str | None = None
    severity_id: int | None = None

    status: EventStatus

    network: NetworkInfo | None = None
    user: UserInfo | None = None
    process: ProcessInfo | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
    normalized_event: dict[str, Any] | None = None

    processing_metadata: ProcessingMetadata = Field(default_factory=ProcessingMetadata)
    structural_fingerprint: StructuralFingerprint | None = None
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
