"""Drift detection endpoints (Phase 5): baseline inspection and the human
review action. The review queue itself is the existing
GET /events?status=UNDER_REVIEW listing."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schema.drift import BaselineListResponse, BaselineResponse, DriftAcceptRequest, DriftAcceptResponse
from app.schema.ocsf import UniversalEvent
from app.services import drift_service, ingestion_service

router = APIRouter(tags=["drift"])


@router.get("/drift/baselines", response_model=BaselineListResponse)
def list_baselines(db: Session = Depends(get_db)) -> BaselineListResponse:
    items = drift_service.list_baselines(db)
    return BaselineListResponse(total=len(items), items=items)


@router.get("/drift/baselines/{source_key}", response_model=BaselineResponse)
def get_baseline(source_key: str, db: Session = Depends(get_db)) -> BaselineResponse:
    baseline = drift_service.get_baseline(db, source_key)
    if baseline is None:
        raise HTTPException(status_code=404, detail=f"No baseline for source '{source_key}'")
    return baseline


@router.post("/events/{event_id}/drift/accept", response_model=DriftAcceptResponse)
def accept_drift(
    event_id: str, request: DriftAcceptRequest, db: Session = Depends(get_db)
) -> DriftAcceptResponse:
    event = ingestion_service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    try:
        updated, baseline = drift_service.accept(db, event, request.mode, request.note)
    except drift_service.DriftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DriftAcceptResponse(
        event=UniversalEvent.model_validate(updated, from_attributes=True),
        baseline=drift_service.to_response(db, baseline),
    )
