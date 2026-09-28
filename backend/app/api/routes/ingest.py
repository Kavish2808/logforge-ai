"""Ingestion endpoints. Routes stay thin: all logic lives in ingestion_service."""
from __future__ import annotations

import json
from typing import Any
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schema.ingest import BatchIngestRequest, BatchIngestResponse, IngestRequest
from app.schema.ocsf import UniversalEvent
from app.services import ingestion_service

router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=201)
@router.post("/api/v1/ingest", status_code=201)
async def ingest(request: Request, db: Session = Depends(get_db)) -> Any:
    body = await request.json()
    if isinstance(body, dict) and "events" in body:
        # Flexible multi-event format from console
        source = body.get("source", "default")
        events_list = body.get("events", [])
        raw_lines = [e.get("raw") if isinstance(e, dict) else str(e) for e in events_list if e]
        if not raw_lines:
            return {"accepted": 0, "duplicates": 0, "total": 0}
        outcome = ingestion_service.ingest_batch(db, raw_lines)
        return {
            "accepted": len(outcome.results),
            "duplicates": 0,
            "total": outcome.total,
            "success_count": outcome.success_count,
            "partial_count": outcome.partial_count,
            "failed_count": outcome.failed_count,
        }
    
    # Standard single IngestRequest
    raw_log = body.get("raw_log") if isinstance(body, dict) else str(body)
    return ingestion_service.ingest_raw_log(db, raw_log)


@router.post("/ingest/batch", response_model=BatchIngestResponse, status_code=201)
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
