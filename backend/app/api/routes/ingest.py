"""Ingestion endpoints. Routes stay thin: all logic lives in ingestion_service."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schema.ingest import BatchIngestRequest, BatchIngestResponse, IngestRequest
from app.schema.ocsf import UniversalEvent
from app.services import ingestion_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=UniversalEvent, status_code=201)
def ingest(request: IngestRequest, db: Session = Depends(get_db)) -> UniversalEvent:
    return ingestion_service.ingest_raw_log(db, request.raw_log)


@router.post("/batch", response_model=BatchIngestResponse, status_code=201)
def ingest_batch(request: BatchIngestRequest, db: Session = Depends(get_db)) -> BatchIngestResponse:
    outcome = ingestion_service.ingest_batch(db, [item.raw_log for item in request.logs])
    return BatchIngestResponse(
        total=outcome.total,
        success_count=outcome.success_count,
        partial_count=outcome.partial_count,
        failed_count=outcome.failed_count,
        results=outcome.results,
    )
