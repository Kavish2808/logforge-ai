"""Adaptive unknown-vendor onboarding endpoints. Routes stay thin: all
logic and every governance rule live in onboarding_service."""
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.governance.deps import acting_as, current_actor
from app.schema.onboarding import (
    AdapterListResponse,
    AdapterResponse,
    ApproveRequest,
    ApproveResponse,
    CreateSessionRequest,
    RejectRequest,
    RollbackRequest,
    SessionListResponse,
    SessionResponse,
    SubmitProposalRequest,
    SuggestRequest,
)
from app.services import onboarding_service as svc
from app.services.auth_service import Actor

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

T = TypeVar("T")


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except svc.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/sessions", response_model=SessionResponse, status_code=201)
def create_session(request: CreateSessionRequest, db: Session = Depends(get_db)) -> SessionResponse:
    session = _call(lambda: svc.create_session(db, samples=request.samples, event_ids=request.event_ids, name=request.name))
    return svc.session_response(db, session)


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)
) -> SessionListResponse:
    items, total = svc.onboarding_repo.list_sessions(db, limit=limit, offset=offset)
    return SessionListResponse(total=total, limit=limit, offset=offset, items=[svc.session_summary(s) for s in items])


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionResponse:
    return svc.session_response(db, _call(lambda: svc.get_session(db, session_id)))


@router.post("/sessions/{session_id}/suggest", response_model=SessionResponse)
def suggest(session_id: str, request: SuggestRequest, db: Session = Depends(get_db)) -> SessionResponse:
    session = _call(lambda: svc.suggest(db, svc.get_session(db, session_id), request.provider))
    return svc.session_response(db, session)


@router.put("/sessions/{session_id}/proposal", response_model=SessionResponse)
def submit_proposal(session_id: str, request: SubmitProposalRequest, db: Session = Depends(get_db)) -> SessionResponse:
    session = _call(lambda: svc.submit_proposal(db, svc.get_session(db, session_id), request.proposal))
    return svc.session_response(db, session)


@router.post("/sessions/{session_id}/approve", response_model=ApproveResponse)
def approve(session_id: str, request: ApproveRequest, db: Session = Depends(get_db),
            actor: Actor = Depends(current_actor)) -> ApproveResponse:
    session, row = _call(
        lambda: svc.approve(
            db,
            svc.get_session(db, session_id),
            proposal_version=request.proposal_version,
            adapter_id=request.adapter_id,
            approved_by=acting_as(request.approved_by, actor),
            note=request.note,
        )
    )
    return ApproveResponse(session=svc.session_response(db, session), adapter=svc.adapter_version_response(row))


@router.post("/sessions/{session_id}/reject", response_model=SessionResponse)
def reject(session_id: str, request: RejectRequest, db: Session = Depends(get_db),
           actor: Actor = Depends(current_actor)) -> SessionResponse:
    session = _call(
        lambda: svc.reject(db, svc.get_session(db, session_id), reason=request.reason, rejected_by=acting_as(request.rejected_by, actor))
    )
    return svc.session_response(db, session)


@router.get("/adapters", response_model=AdapterListResponse)
def list_adapters(db: Session = Depends(get_db)) -> AdapterListResponse:
    items = [_adapter(db, adapter_id) for adapter_id in svc.onboarding_repo.adapter_ids(db)]
    return AdapterListResponse(total=len(items), items=items)


@router.get("/adapters/{adapter_id}", response_model=AdapterResponse)
def get_adapter(adapter_id: str, db: Session = Depends(get_db)) -> AdapterResponse:
    response = _adapter(db, adapter_id)
    if not response.versions:
        raise HTTPException(status_code=404, detail=f"Onboarded adapter '{adapter_id}' not found")
    return response


@router.post("/adapters/{adapter_id}/rollback", response_model=AdapterResponse)
def rollback(adapter_id: str, request: RollbackRequest, db: Session = Depends(get_db),
             actor: Actor = Depends(current_actor)) -> AdapterResponse:
    _call(lambda: svc.rollback(db, adapter_id, reason=request.reason, requested_by=acting_as(request.requested_by, actor)))
    return _adapter(db, adapter_id)


def _adapter(db: Session, adapter_id: str) -> AdapterResponse:
    versions = svc.onboarding_repo.adapter_versions(db, adapter_id)
    active = next((v.version for v in versions if v.status == "ACTIVE"), None)
    return AdapterResponse(
        adapter_id=adapter_id,
        active_version=active,
        versions=[svc.adapter_version_response(v) for v in versions],
    )
