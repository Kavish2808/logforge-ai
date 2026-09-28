"""Request models for the Phase 8 APIs (compact lineage, statistical /
semantic drift, golden baselines). Responses are plain dicts built by the
services, documented in docs/phase8-lineage-drift-golden.md."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StatisticalAnalyzeRequest(BaseModel):
    source_key: str | None = Field(default=None, max_length=128,
                                   description="One source (adapter_id); omitted = every source active in the current window.")
    window_end: datetime | None = Field(default=None, description="End of the current window; default = start of this UTC hour.")
    baseline_hours: int = Field(default=168, ge=1, le=24 * 90)
    current_hours: int = Field(default=1, ge=1, le=24 * 7)
    extension_fields: list[str] = Field(default_factory=list, max_length=10,
                                        description="Opt-in extensions.* keys to monitor in addition to the defaults.")


class FindingAcknowledgeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class GoldenPinRequest(BaseModel):
    note: str = Field(default="", max_length=2000, description="Required, non-empty: why this is the trusted reference.")
    profile_hours: int = Field(default=168, ge=1, le=24 * 90)
    window_end: datetime | None = None


class GoldenRepinRequest(GoldenPinRequest):
    expected_version: int | None = Field(default=None, ge=1,
                                         description="Optimistic concurrency: the active golden version being replaced.")


class GoldenRetireRequest(BaseModel):
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


# --- Steps 6-8 -------------------------------------------------------------------------------------------


class ShadowRunRequest(BaseModel):
    learning_session_id: str = Field(..., min_length=1, max_length=26)


class ReplayJobRequest(BaseModel):
    adapter_id: str = Field(..., min_length=1, max_length=128, description="Source (adapter id) whose events are replayed.")
    reason: str = Field(default="", max_length=2000, description="Required, non-empty.")
    from_version: str | None = Field(default=None, max_length=32,
                                     description="Only events processed with this adapter version (default: any).")
    window_start: datetime | None = None
    window_end: datetime | None = None
    rate_per_sec: float | None = Field(default=None, gt=0, le=1000)
    rate_per_minute: float | None = Field(default=None, gt=0, le=60000)
    batch_size: int = Field(default=100, ge=1, le=1000)


class RevisionRollbackRequest(BaseModel):
    reason: str = Field(default="", max_length=2000, description="Required, non-empty.")
    replay: bool = Field(default=True, description="Queue a ROLLBACK replay job (PENDING; started explicitly).")
    rate_per_sec: float = Field(default=50.0, gt=0, le=1000)
    batch_size: int = Field(default=100, ge=1, le=1000)


class CorrelationAnalyzeRequest(BaseModel):
    window_end: datetime | None = Field(default=None, description="Default: now, truncated to the minute.")
    window_minutes: int = Field(default=60, ge=5, le=24 * 60)
