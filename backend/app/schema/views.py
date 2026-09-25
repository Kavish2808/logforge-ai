"""Response models for the read-only operational intelligence layer
(/api/v1/views). Everything here is derived from stored data; nothing is
estimated or invented."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventRow(BaseModel):
    """Compact, table-friendly event summary (no raw payload)."""

    event_id: str
    received_at: datetime
    event_timestamp: datetime | None
    status: str
    format_detected: str
    vendor: str | None
    product: str | None
    source_key: str | None  # adapter_id, or the suspected vendor source for POSSIBLE_FORMAT_DRIFT
    adapter_id: str | None
    adapter_version: str | None
    adapter_source: str | None  # "manual" (shipped YAML) | "onboarded"
    ocsf_class_name: str | None
    ocsf_category_name: str | None
    event_type: str | None
    event_action: str | None
    severity: str | None
    drift_status: str | None
    drift_severity: str | None
    warning_count: int
    preserved_field_count: int
    raw_hash: str


class EventPage(BaseModel):
    items: list[EventRow]
    limit: int
    next_cursor: str | None = Field(description="Opaque cursor for the next (older) page; null when exhausted.")
    has_more: bool
    total: int | None = Field(default=None, description="Only when include_total=true (a COUNT over the filter).")


class Summary(BaseModel):
    window: dict[str, Any]
    totals: dict[str, int]
    by_status: dict[str, int]
    by_format: dict[str, int]
    by_vendor: dict[str, int]
    by_adapter: dict[str, int]
    by_drift_status: dict[str, int]
    by_drift_severity: dict[str, int]
    adapters: dict[str, int]
    onboarding_sessions: dict[str, int]
    learning_sessions: dict[str, int]
    trend: list[dict[str, Any]]


class FilterValues(BaseModel):
    statuses: list[str]
    formats: list[str]
    vendors: list[str]
    products: list[str]
    sources: list[str]
    adapters: list[dict[str, Any]]
    categories: list[str]
    severities: list[str]
    drift_statuses: list[str]
    drift_severities: list[str]


class LineageStage(BaseModel):
    stage: str
    outcome: str  # OK | WARN | FAIL | SKIPPED
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class Lineage(BaseModel):
    event_id: str
    status: str
    chain: list[LineageStage]
    integrity: dict[str, Any]
    field_accounting: dict[str, Any]
    nothing_silently_discarded: bool
    basis: list[str]


class SourceSummary(BaseModel):
    source_key: str
    kind: str  # shipped_vendor | shipped_generic | onboarded | unknown
    vendor: str | None
    product: str | None
    events: dict[str, int]
    partial_rate: float | None
    formats: dict[str, int]
    adapter_versions_seen: dict[str, int]
    active_version: str | None
    baseline: dict[str, Any] | None
    drift: dict[str, int]
    under_review: int
    learning_sessions: dict[str, int]
    last_seen: datetime | None


class SourceList(BaseModel):
    total: int
    items: list[SourceSummary]


class SourceDetail(SourceSummary):
    versions: list[dict[str, Any]]
    baseline_history: list[dict[str, Any]]
    recent_drift: list[dict[str, Any]]


class TimelineEntry(BaseModel):
    at: datetime
    phase: str  # "onboarding" | "drift" | "learning" | "baseline" | "adapter"
    kind: str
    title: str
    details: dict[str, Any] = Field(default_factory=dict)
    refs: dict[str, Any] = Field(default_factory=dict)


class Timeline(BaseModel):
    source_key: str
    entries: list[TimelineEntry]
