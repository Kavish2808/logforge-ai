"""Request/response models for the ingestion API."""
from pydantic import BaseModel, Field, field_validator

from app.schema.ocsf import UniversalEvent

# A single raw log capped at 256 KB: comfortably larger than any real
# syslog/CEF/JSON log line, while preventing one request from allocating
# unbounded memory. A batch is capped at 1000 items for the same reason.
MAX_RAW_LOG_LENGTH = 256_000
MAX_BATCH_SIZE = 1000


class IngestRequest(BaseModel):
    raw_log: str = Field(
        ...,
        min_length=1,
        max_length=MAX_RAW_LOG_LENGTH,
        description="The raw, unmodified log line/event.",
    )
    source_hint: str | None = Field(
        default=None,
        description="Optional hint (e.g. vendor/product name) to help adapter selection.",
    )

    @field_validator("raw_log")
    @classmethod
    def _reject_nul_bytes(cls, value: str) -> str:
        # Postgres TEXT columns cannot store NUL (0x00); rejecting it here,
        # at the boundary, is far clearer than a downstream database error.
        if "\x00" in value:
            raise ValueError("raw_log must not contain NUL (0x00) characters")
        return value


class BatchIngestRequest(BaseModel):
    logs: list[IngestRequest] = Field(..., min_length=1, max_length=MAX_BATCH_SIZE)


class BatchIngestResponse(BaseModel):
    total: int
    success_count: int
    partial_count: int
    failed_count: int
    results: list[UniversalEvent]


class EventListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[UniversalEvent]


class ReprocessResponse(BaseModel):
    event: UniversalEvent
    reprocessed: bool
