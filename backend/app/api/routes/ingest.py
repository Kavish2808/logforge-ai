"""Ingestion endpoints. Routes stay thin: all logic lives in ingestion_service."""
from __future__ import annotations

import json
from typing import Any
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.governance.deps import require_ingest
from app.schema.ingest import MAX_BATCH_SIZE, MAX_RAW_LOG_LENGTH, BatchIngestRequest, BatchIngestResponse, IngestRequest
from app.schema.ocsf import UniversalEvent
from app.services import ingestion_service

router = APIRouter(tags=["ingest"], dependencies=[Depends(require_ingest)])


class _ConsoleEvent(BaseModel):
    raw: str


class _ConsoleIngestRequest(BaseModel):
    source: str = "default"
    events: list[_ConsoleEvent | str] = Field(..., max_length=10000)


def _validate(model: type[BaseModel], data: bytes | dict) -> Any:
    # Validate inside the route (the body shape decides the model) but keep the
    # standard 422 VALIDATION_ERROR envelope that typed body parameters produce.
    try:
        return model.model_validate_json(data) if isinstance(data, bytes) else model.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError([{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]) from exc


@router.post("/ingest", status_code=201)
@router.post("/api/ingest", status_code=201)
@router.post("/api/v1/ingest", status_code=201)
async def ingest(request: Request, db: Session = Depends(get_db)) -> Any:
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "").lower()
    source_header = request.headers.get("x-logforge-source") or request.headers.get("x-source") or "default"

    body: Any = None
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        # Handle raw CSV, TSV, or plain text log streams directly
        text_content = raw_body.decode("utf-8", errors="replace").strip()
        if text_content and ("\n" in text_content or "," in text_content or "csv" in content_type or "plain" in content_type):
            raw_lines = [line.strip() for line in text_content.splitlines() if line.strip()]
            if raw_lines:
                for idx, line in enumerate(raw_lines):
                    if len(line) > MAX_RAW_LOG_LENGTH:
                        raise RequestValidationError([{"loc": ("body", idx), "msg": f"Line exceeds maximum length of {MAX_RAW_LOG_LENGTH}", "type": "string_too_long"}])
                    if "\x00" in line:
                        raise RequestValidationError([{"loc": ("body", idx), "msg": "Line contains NUL byte", "type": "value_error"}])
                
                total_accepted = 0
                all_results = []
                s_count, p_count, f_count = 0, 0, 0
                chunk_size = 1000
                for i in range(0, len(raw_lines), chunk_size):
                    chunk = raw_lines[i:i + chunk_size]
                    outcome = ingestion_service.ingest_batch(db, chunk, source=source_header)
                    total_accepted += len(outcome.results)
                    s_count += outcome.success_count
                    p_count += outcome.partial_count
                    f_count += outcome.failed_count
                    all_results.extend(outcome.results)

                return {
                    "accepted": total_accepted,
                    "duplicates": 0,
                    "total": len(raw_lines),
                    "success_count": s_count,
                    "partial_count": p_count,
                    "failed_count": f_count,
                    "results": all_results[:100] if len(all_results) > 100 else all_results,
                }
        raise RequestValidationError([{"loc": ("body",), "msg": "Invalid JSON or CSV body", "type": "json_invalid"}])

    if isinstance(body, dict) and "events" in body:
        # Flexible multi-event format from console
        console = _validate(_ConsoleIngestRequest, raw_body)
        raw_lines = [e.raw if isinstance(e, _ConsoleEvent) else e for e in console.events]
        raw_lines = [line for line in raw_lines if line]
        if not raw_lines:
            return {"accepted": 0, "duplicates": 0, "total": 0}

        # Unnest/flatten any multi-line blocks (e.g. multi-line CSV/log payload passed inside a single event slot)
        flattened_lines: list[str] = []
        for line in raw_lines:
            if isinstance(line, str) and ("\n" in line or "\r" in line):
                for sub in line.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                    sub_clean = sub.strip()
                    if sub_clean:
                        flattened_lines.append(sub_clean)
            elif isinstance(line, str):
                line_clean = line.strip()
                if line_clean:
                    flattened_lines.append(line_clean)
            else:
                flattened_lines.append(str(line))
        raw_lines = flattened_lines
        if not raw_lines:
            return {"accepted": 0, "duplicates": 0, "total": 0}

        for idx, line in enumerate(raw_lines):
            if len(line) > MAX_RAW_LOG_LENGTH:
                # If a line exceeds 256,000 characters, truncate gracefully with a warning indicator rather than failing the whole ingestion
                line = line[:MAX_RAW_LOG_LENGTH - 16] + "...[TRUNCATED]"
                raw_lines[idx] = line
            if "\x00" in line:
                line = line.replace("\x00", "")
                raw_lines[idx] = line

        source_name = getattr(console, "source", None) or source_header
        total_accepted = 0
        all_results = []
        s_count, p_count, f_count = 0, 0, 0
        chunk_size = 1000
        for i in range(0, len(raw_lines), chunk_size):
            chunk = raw_lines[i:i + chunk_size]
            outcome = ingestion_service.ingest_batch(db, chunk, source=source_name)
            total_accepted += len(outcome.results)
            s_count += outcome.success_count
            p_count += outcome.partial_count
            f_count += outcome.failed_count
            all_results.extend(outcome.results)

        return {
            "accepted": total_accepted,
            "duplicates": 0,
            "total": len(raw_lines),
            "success_count": s_count,
            "partial_count": p_count,
            "failed_count": f_count,
            "results": all_results[:100] if len(all_results) > 100 else all_results,
        }
    
    # Standard single IngestRequest
    single = _validate(IngestRequest, raw_body)
    # Check if a single raw_log contains multiple lines (e.g. pasted CSV block)
    if "\n" in single.raw_log:
        split_lines = [l.strip() for l in single.raw_log.splitlines() if l.strip()]
        if len(split_lines) > 1:
            outcome = ingestion_service.ingest_batch(db, split_lines, source=single.source_hint or source_header)
            return {
                "accepted": len(outcome.results),
                "duplicates": 0,
                "total": outcome.total,
                "success_count": outcome.success_count,
                "partial_count": outcome.partial_count,
                "failed_count": outcome.failed_count,
                "results": outcome.results,
            }
    return ingestion_service.ingest_raw_log(db, single.raw_log, source=single.source_hint)


@router.post("/ingest/batch", response_model=BatchIngestResponse, status_code=201)
@router.post("/api/ingest/batch", response_model=BatchIngestResponse, status_code=201)
@router.post("/api/v1/ingest/batch", response_model=BatchIngestResponse, status_code=201)
def ingest_batch(request: BatchIngestRequest, db: Session = Depends(get_db)) -> BatchIngestResponse:
    outcome = ingestion_service.ingest_batch(db, [item.raw_log for item in request.logs])
    return BatchIngestResponse(
        total=outcome.total,
        success_count=outcome.success_count,
        partial_count=outcome.partial_count,
        failed_count=outcome.failed_count,
        under_review_count=outcome.under_review_count,
        results=outcome.results,
    )


@router.post("/ingest/demo", status_code=201)
@router.post("/api/ingest/demo", status_code=201)
@router.post("/api/v1/ingest/demo", status_code=201)
def ingest_demo(db: Session = Depends(get_db)) -> dict[str, Any]:
    groups = {
        "fortinet-demo": [
            f'date=2026-09-28 time=10:00:{i:02d} devname="edge-fw" type="traffic" subtype="forward" srcip=10.0.1.{i+1} dstip=203.0.113.25 srcport={41000+i} dstport=443 action="accept" proto=6 policyid=12'
            for i in range(6)
        ],
        "cisco-demo": [
            f'<166>Sep 28 10:02:{i:02d} asa-fw %ASA-6-302013: Built outbound TCP connection {2000+i} for outside:203.0.113.9/443 (203.0.113.9/443) to inside:10.0.0.{i+1}/{51000+i} (10.0.0.{i+1}/{51000+i})'
            for i in range(6)
        ],
        "gateway-demo": [
            json.dumps({
                "timestamp": f"2026-09-28T10:05:{i:02d}Z",
                "vendor": "Gateway",
                "event_type": "network",
                "src_ip": f"10.10.1.{i+1}",
                "dst_ip": "203.0.113.10",
                "dst_port": 443,
                "action": "allow",
                "bytes": 1200 + i * 100,
                **({"risk_score": 81, "policy_version": "v2"} if i > 3 else {}),
            })
            for i in range(6)
        ],
        "cef-demo": [
            f'CEF:0|Acme|Edge|1.0|100|Connection allowed|5|src=10.2.0.{i+1} dst=203.0.113.4 dpt=443 act=allow customLabel=preserved'
            for i in range(4)
        ],
        "leef-demo": [
            f'LEEF:2.0|Acme|Sensor|2.0|{700+i}|^|src=10.3.0.{i+1}^dst=203.0.113.6^action=blocked^policy=external'
            for i in range(3)
        ],
    }
    all_logs: list[str] = []
    for lines in groups.values():
        all_logs.extend(lines)

    outcome = ingestion_service.ingest_batch(db, all_logs)
    return {
        "accepted": len(outcome.results),
        "duplicates": 0,
        "total": outcome.total,
        "success_count": outcome.success_count,
        "partial_count": outcome.partial_count,
        "failed_count": outcome.failed_count,
        "message": f"Sample dataset loaded successfully ({len(outcome.results)} events).",
    }
