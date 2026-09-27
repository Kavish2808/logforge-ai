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

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.pipeline.drift.explain import render_explanation


class EventStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNDER_REVIEW = "UNDER_REVIEW"


class FormatType(StrEnum):
    SYSLOG = "syslog"
    JSON = "json"
    CEF = "cef"
    # Formats of onboarded sources, parsed by human-approved declarative
    # parsers; never returned by the built-in detector.
    DELIMITED = "delimited"
    KV = "kv"
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


class DriftStatus(StrEnum):
    NORMAL = "NORMAL"
    DRIFT = "DRIFT"
    BASELINE_CREATED = "BASELINE_CREATED"  # this event bootstrapped its source's baseline
    ERROR = "ERROR"  # drift evaluation itself failed; event status left untouched
    # A generic-adapter event carries deterministic evidence of a previously
    # known vendor source (its vendor adapter no longer matched). "Possible",
    # never definite: generic adapters legitimately receive unrelated data.
    POSSIBLE_FORMAT_DRIFT = "POSSIBLE_FORMAT_DRIFT"


class DriftCriticalFieldChange(BaseModel):
    field: str  # raw (parser-level) field name
    target: str | None = None  # OCSF target it maps to, when resolved via the adapter
    change: str  # "removed" | "type_changed"
    baseline_type: str | None = None
    current_type: str | None = None


class DriftEvidence(BaseModel):
    reason: str  # MATCH_FIELD_NEAR_MISS | VENDOR_IDENTITY_FIELD_IN_OTHER_FORMAT | VENDOR_SIGNATURE_IN_RAW
    detail: str | None = None


class DriftDifferences(BaseModel):
    added_fields: list[str] = Field(default_factory=list)
    removed_fields: list[str] = Field(default_factory=list)
    field_count: dict[str, int] = Field(default_factory=dict)  # {"baseline": n, "current": m}
    order_changed: bool = False
    type_changes: dict[str, dict[str, str]] = Field(default_factory=dict)
    format_changed: dict[str, str] | None = None


class DriftReview(BaseModel):
    resolution: str  # "accepted_variant" | "replaced_baseline" | "acknowledged"
    reviewed_at: datetime
    note: str | None = None
    baseline_version: int


class DriftMetadata(BaseModel):
    """Phase 5 drift evaluation result, stored at processing_metadata.drift.
    Present only for events of known vendor sources that were evaluated,
    and for generic-adapter events flagged as POSSIBLE_FORMAT_DRIFT."""

    status: DriftStatus
    # Known vendor source (== vendor adapter_id). For POSSIBLE_FORMAT_DRIFT,
    # the previously known source the event is suspected to come from.
    source_key: str
    baseline_version: int | None = None
    baseline_origin: str | None = None  # "auto_bootstrap" | "human_review"
    matched: str | None = None  # "reference" | "variant:<n>"
    similarity: float | None = None
    threshold: float | None = None
    components: dict[str, float] | None = None
    differences: DriftDifferences | None = None
    original_status: str | None = None  # event status before it was set to UNDER_REVIEW
    reonboarding_required: bool = False
    recommended_action: str | None = None  # human-readable guidance
    evaluated_at: datetime | None = None
    review: DriftReview | None = None
    error: str | None = None

    # --- drift intelligence (all deterministic, derived from structural evidence) ---
    change_types: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    severity: str | None = None  # LOW | MEDIUM | HIGH | CRITICAL
    severity_score: int | None = None
    severity_factors: dict[str, int] | None = None
    critical_field_changes: list[DriftCriticalFieldChange] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)  # REVIEW_* codes
    current_adapter: str | None = None  # POSSIBLE_FORMAT_DRIFT: the generic adapter used
    evidence: list[DriftEvidence] = Field(default_factory=list)  # POSSIBLE_FORMAT_DRIFT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def explanation(self) -> str:
        """Human-readable report, rendered from the structured fields above."""
        return render_explanation(self)


class ProcessingMetadata(BaseModel):
    parser: str | None = None
    pipeline_version: str = "0.1.0"
    duration_ms: float | None = None
    adapter_source: str | None = None  # "manual" (shipped YAML) | "onboarded" (human-approved onboarding)
    drift: DriftMetadata | None = None
    # Phase 7 (additive): present only when extensions exceeded the inline
    # budget; `extensions` then holds the inline part and the rest is stored
    # losslessly in extension overflow storage (GET /integrity/extensions/{id}).
    extension_spill: dict[str, Any] | None = None


class StructuralFingerprint(BaseModel):
    field_set: list[str] = Field(default_factory=list)
    field_order: list[str] = Field(default_factory=list)
    field_count: int = 0
    signature: str | None = None
    field_types: dict[str, str] | None = None  # Phase 5; absent on older events


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
