"""Request models for the Phase 7 APIs (auth, governance, integrity, alerts,
export). Every input is bounded and validated; unknown keys are rejected."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ULID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(_Strict):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class CreateUserRequest(Credentials):
    role: Literal["ANALYST", "SECURITY_ENGINEER", "SOC_ADMIN"]


class UpdateUserRequest(_Strict):
    role: Literal["ANALYST", "SECURITY_ENGINEER", "SOC_ADMIN"] | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> "UpdateUserRequest":
        if self.role is None and self.active is None:
            raise ValueError("provide role and/or active")
        return self


class ConfigUpdateRequest(_Strict):
    review_sla: dict[str, Any] | None = None
    alert_thresholds: dict[str, float] | None = None

    @model_validator(mode="after")
    def _something(self) -> "ConfigUpdateRequest":
        if self.review_sla is None and self.alert_thresholds is None:
            raise ValueError("provide review_sla and/or alert_thresholds")
        return self


class SealRequest(_Strict):
    force: bool = Field(
        default=False,
        description="Seal every unsealed event now, ignoring the grace period.",
    )


class AckRequest(_Strict):
    note: str | None = Field(default=None, max_length=1000)


class ExportRequest(_Strict):
    output: Literal["ndjson", "json"] = Field(
        default="ndjson",
        description="Output format.",
    )
    limit: int | None = Field(default=None, ge=1, le=1_000_000)
    cursor: str | None = Field(default=None, max_length=512)
    include_raw: bool = False
    event_ids: list[str] = Field(default_factory=list, max_length=1000)
    start: datetime | None = None
    end: datetime | None = None
    status: list[
        Literal["SUCCESS", "PARTIAL", "FAILED", "UNDER_REVIEW"]
    ] | None = Field(default=None, max_length=4)
    source: str | None = Field(default=None, max_length=128)
    vendor: str | None = Field(default=None, max_length=128)
    product: str | None = Field(default=None, max_length=128)
    format: str | None = Field(
        default=None,
        max_length=32,
        description="Log format filter (as in /views/events).",
    )
    adapter_id: str | None = Field(default=None, max_length=128)
    adapter_version: str | None = Field(default=None, max_length=32)
    drift_status: str | None = Field(default=None, max_length=32)
    drift_severity: str | None = Field(default=None, max_length=16)
    severity: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=128)
    search: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
    )

    @field_validator("event_ids")
    @classmethod
    def _ulids(cls, ids: list[str]) -> list[str]:
        import re

        for i in ids:
            if not re.match(ULID_PATTERN, i):
                raise ValueError(f"invalid event id {i[:40]!r}")
        return ids

    @model_validator(mode="after")
    def _window(self) -> "ExportRequest":
        if self.start and self.end and self.start >= self.end:
            raise ValueError("'start' must be earlier than 'end'")
        return self