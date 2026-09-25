"""Request/response models for the Phase 6 learning API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProposeRequest(BaseModel):
    assistant: Literal["auto", "anthropic", "offline"] = Field(
        default="auto", description="Optional LLM assistant for unresolved fields; learning works fully offline without it."
    )
    requested_by: str | None = Field(default=None, max_length=128)


class SubmitDeltaRequest(BaseModel):
    proposal: dict[str, Any] = Field(..., description="A LearningDelta; validated exactly like a generated one.")
    submitted_by: str | None = Field(default=None, max_length=128)


class ApproveRequest(BaseModel):
    proposal_version: int = Field(..., ge=1)
    approved_by: str | None = Field(default=None, max_length=128, description="Free-text reviewer identity (no authentication in this MVP).")
    note: str | None = Field(default=None, max_length=1000)
    confirm_supersede: bool = Field(
        default=False,
        description="Required when historical compatibility is below threshold: confirms the new version intentionally supersedes old behavior.",
    )
    activate: bool = Field(default=False, description="APPROVE & ACTIVATE in one explicit action.")


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
    by: str | None = Field(default=None, max_length=128)


class ActivateRequest(BaseModel):
    activated_by: str | None = Field(default=None, max_length=128)


class RollbackRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
    requested_by: str | None = Field(default=None, max_length=128)


class LearningSessionResponse(BaseModel):
    id: str
    status: str
    source_key: str
    source_adapter_id: str
    source_adapter_version: int
    target_version: int | None
    target_version_status: str | None  # live status of the created version (ACTIVE / SUPERSEDED / ROLLED_BACK)
    trigger_event_id: str
    trigger_raw_hash: str
    drift: dict[str, Any]
    old_fingerprint: dict[str, Any] | None
    new_fingerprint: dict[str, Any]
    evidence: dict[str, Any]
    learning_modes: list[str]
    risk: str
    risk_reasons: list[str]
    proposal: dict[str, Any] | None
    proposal_version: int
    proposal_source: str | None
    assistant: dict[str, Any] | None
    candidate: dict[str, Any] | None
    mapping_diff: dict[str, Any] | None
    validation: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    approved_by: str | None
    approved_at: datetime | None
    activated_at: datetime | None
    rejection_reason: str | None
    recommendation: str
    report: str
    created_at: datetime
    updated_at: datetime


class LearningSessionSummary(BaseModel):
    id: str
    status: str
    source_key: str
    source_adapter_version: int
    target_version: int | None
    risk: str
    learning_modes: list[str]
    validation_result: str | None
    trigger_event_id: str
    created_at: datetime


class LearningSessionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LearningSessionSummary]
