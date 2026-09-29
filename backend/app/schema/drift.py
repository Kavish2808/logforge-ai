"""Request/response models for the drift detection API (Phase 5).

The per-event drift record itself (DriftMetadata) lives in
app.schema.ocsf because it is embedded in UniversalEvent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schema.ocsf import StructuralFingerprint, UniversalEvent


class AcceptedVariant(BaseModel):
    fingerprint: StructuralFingerprint
    accepted_from_event_id: str | None = None
    accepted_at: datetime | None = None
    accepted_in_version: int | None = None  # baseline version this variant introduced
    note: str | None = None


class BaselineHistoryEntry(BaseModel):
    """One step of a source's structural evolution (no event copies):
    v1 BASELINE_CREATED -> v2 VARIANT_ADDED -> v3 BASELINE_REPLACED ..."""

    model_config = ConfigDict(from_attributes=True)

    version: int
    action: str  # BASELINE_CREATED | VARIANT_ADDED | BASELINE_REPLACED | HISTORY_STARTED
    event_id: str | None = None
    signature: str | None = None
    field_count: int | None = None
    # Structural change relative to the reference in force before this step:
    # {"added_fields", "removed_fields", "type_changes", "order_changed", "change_types"}
    changes: dict[str, Any] | None = None
    note: str | None = None
    created_at: datetime


class BaselineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_key: str
    adapter_id: str
    adapter_version: str | None = None
    format_detected: str
    origin: str  # "auto_bootstrap" (provisional, first observed structure) | "human_review"
    version: int
    fingerprint: StructuralFingerprint
    accepted_variants: list[AcceptedVariant] = Field(default_factory=list)
    created_from_event_id: str | None = None
    created_at: datetime
    updated_at: datetime
    under_review_count: int = 0
    # Critical-field value shapes a human accepted for this source ({raw field: [shape, ...]}).
    accepted_value_shapes: dict[str, list[str]] = Field(default_factory=dict)
    # Structural evolution, oldest first. Included on single-baseline and
    # accept responses; omitted (null) from the list endpoint.
    history: list[BaselineHistoryEntry] | None = None


class BaselineListResponse(BaseModel):
    total: int
    items: list[BaselineResponse]


class DriftAcceptRequest(BaseModel):
    mode: Literal["add_variant", "replace_baseline", "acknowledge"] = Field(
        ...,
        description=(
            "add_variant: accept this event's structure as an additional valid structure for the source. "
            "replace_baseline: make this event's structure the new reference and clear existing variants. "
            "acknowledge: mark the event reviewed and restore its status without changing the baseline "
            "(the only mode for POSSIBLE_FORMAT_DRIFT)."
        ),
    )
    note: str | None = Field(default=None, max_length=1000, description="Optional reviewer note.")


class DriftAcceptResponse(BaseModel):
    event: UniversalEvent
    baseline: BaselineResponse
