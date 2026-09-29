"""Event query and reprocessing endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schema.ingest import EventListResponse, ReprocessResponse
from app.schema.ocsf import UniversalEvent
from app.services import ingestion_service

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventListResponse)
def list_events(
    vendor: str | None = None,
    status: str | None = None,
    format_detected: str | None = Query(default=None, alias="format"),
    adapter_id: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> EventListResponse:
    items, total = ingestion_service.list_events(
        db,
        vendor=vendor,
        status=status,
        format_detected=format_detected,
        adapter_id=adapter_id,
        source=source,
        q=q,
        limit=limit,
        offset=offset,
    )
    return EventListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/{event_id}", response_model=UniversalEvent)
def get_event(event_id: str, db: Session = Depends(get_db)) -> UniversalEvent:
    event = ingestion_service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return UniversalEvent.model_validate(event, from_attributes=True)


@router.post("/{event_id}/reprocess", response_model=ReprocessResponse)
def reprocess_event(event_id: str, db: Session = Depends(get_db)) -> ReprocessResponse:
    event = ingestion_service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    updated = ingestion_service.reprocess_event(db, event)
    return ReprocessResponse(event=updated, reprocessed=True)
