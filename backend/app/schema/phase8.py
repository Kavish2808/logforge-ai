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
