"""Request/response models for the adaptive onboarding API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.onboarding.analysis import MAX_SAMPLE_CHARS, MAX_SAMPLES


class CreateSessionRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    samples: list[str] = Field(default_factory=list, max_length=MAX_SAMPLES,
                               description="Raw sample logs from the unknown source (10-15 recommended).")
    event_ids: list[str] = Field(default_factory=list, max_length=MAX_SAMPLES,
                                 description="Existing events (e.g. FAILED unknown-format events) to use as samples.")

    @field_validator("samples")
    @classmethod
    def _bounded_samples(cls, samples: list[str]) -> list[str]:
        for i, sample in enumerate(samples):
            if not sample.strip():
                raise ValueError(f"sample {i} is empty")
            if len(sample) > MAX_SAMPLE_CHARS:
                raise ValueError(f"sample {i} exceeds {MAX_SAMPLE_CHARS} characters")
            if "\x00" in sample:
                raise ValueError(f"sample {i} contains a NUL (0x00) character")
        return samples


class SuggestRequest(BaseModel):
    provider: Literal["auto", "anthropic", "offline"] = "auto"


class SubmitProposalRequest(BaseModel):
    """A human-written or human-edited proposal; validated exactly like an AI one."""

    proposal: dict[str, Any]


class ApproveRequest(BaseModel):
    proposal_version: int = Field(..., ge=1, description="The proposal version being approved (guards against approving a stale proposal).")
    adapter_id: str | None = Field(default=None, max_length=64, description="Optional adapter id; defaults to vendor_product.")
    approved_by: str | None = Field(default=None, max_length=128, description="Free-text reviewer identity (no authentication in this MVP).")
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
    rejected_by: str | None = Field(default=None, max_length=128)


class RollbackRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
    requested_by: str | None = Field(default=None, max_length=128)


class Activation(BaseModel):
    active: bool
    state: str  # NOT_ACTIVE_NO_PROPOSAL | NOT_ACTIVE_NOT_ELIGIBLE | NOT_ACTIVE_AWAITING_APPROVAL | ACTIVE | REJECTED | ROLLED_BACK
    eligible_for_approval: bool
    adapter_id: str | None = None
    adapter_version: int | None = None


class SessionSummary(BaseModel):
    id: str
    name: str | None
    status: str
    sample_count: int
    proposal_version: int
    validation_result: str | None
    match_rate: float | None
    adapter_id: str | None
    adapter_version: int | None
    created_at: datetime
    updated_at: datetime


class SessionResponse(SessionSummary):
    samples: list[dict[str, Any]]
    analysis: dict[str, Any]
    proposal: dict[str, Any] | None
    proposal_source: str | None
    suggestion_error: dict[str, Any] | None
    validation: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    activation: Activation
    explanation: str


class SessionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SessionSummary]


class AdapterVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    adapter_id: str
    version: int
    status: str
    mapping: dict[str, Any]
    session_id: str
    proposal_version: int
    validation_summary: dict[str, Any]
    approved_by: str | None
    approval_note: str | None
    approved_at: datetime
    deactivated_at: datetime | None
    created_at: datetime


class AdapterResponse(BaseModel):
    adapter_id: str
    active_version: int | None
    versions: list[AdapterVersionResponse]


class AdapterListResponse(BaseModel):
    total: int
    items: list[AdapterResponse]


class ApproveResponse(BaseModel):
    session: SessionResponse
    adapter: AdapterVersionResponse
