"""Phase 6 continuous-learning endpoints. Routes stay thin: the state machine
and every safety rule live in learning_service."""
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.repository import learning_repo
from app.governance.deps import acting_as, current_actor
from app.schema.learning import (
    ActivateRequest,
    ApproveRequest,
    LearningSessionListResponse,
    LearningSessionResponse,
    LearningSessionSummary,
    ProposeRequest,
    ReasonRequest,
    RollbackRequest,
    SubmitDeltaRequest,
)
from app.services import learning_service as svc
from app.services.auth_service import Actor

router = APIRouter(tags=["learning"])
T = TypeVar("T")


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except svc.LearningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _response(db: Session, session) -> LearningSessionResponse:
    return LearningSessionResponse(**svc.session_dict(db, session))


@router.post("/events/{event_id}/learning/propose", response_model=LearningSessionResponse, status_code=201)
def propose(event_id: str, request: ProposeRequest, db: Session = Depends(get_db)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.propose(db, event_id, assistant=request.assistant, requested_by=request.requested_by)))


@router.get("/learning/sessions", response_model=LearningSessionListResponse)
def list_sessions(
    source_key: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> LearningSessionListResponse:
    items, total = learning_repo.list_sessions(db, source_key=source_key, status=status, limit=limit, offset=offset)
    return LearningSessionListResponse(total=total, limit=limit, offset=offset, items=[
        LearningSessionSummary(
            id=s.id, status=s.status, source_key=s.source_key, source_adapter_version=s.source_adapter_version,
            target_version=s.target_version, risk=s.risk, learning_modes=s.learning_modes,
            validation_result=(s.validation or {}).get("result"), trigger_event_id=s.trigger_event_id,
            created_at=s.created_at,
        ) for s in items
    ])


@router.get("/learning/sessions/{session_id}", response_model=LearningSessionResponse)
def get_session(session_id: str, db: Session = Depends(get_db)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.get_session(db, session_id)))


@router.post("/learning/sessions/{session_id}/validate", response_model=LearningSessionResponse)
def validate(session_id: str, db: Session = Depends(get_db)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.validate(db, session_id)))


@router.put("/learning/sessions/{session_id}/proposal", response_model=LearningSessionResponse)
def submit_proposal(session_id: str, request: SubmitDeltaRequest, db: Session = Depends(get_db)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.submit_proposal(db, session_id, request.proposal, request.submitted_by)))


@router.post("/learning/sessions/{session_id}/approve", response_model=LearningSessionResponse)
def approve(session_id: str, request: ApproveRequest, db: Session = Depends(get_db),
            actor: Actor = Depends(current_actor)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.approve(
        db, session_id, proposal_version=request.proposal_version, approved_by=acting_as(request.approved_by, actor),
        note=request.note, confirm_supersede=request.confirm_supersede, activate_now=request.activate,
    )))


@router.post("/learning/sessions/{session_id}/request-review", response_model=LearningSessionResponse)
def request_review(session_id: str, request: ReasonRequest, db: Session = Depends(get_db),
                   actor: Actor = Depends(current_actor)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.request_review(db, session_id, reason=request.reason, requested_by=acting_as(request.by, actor))))


@router.post("/learning/sessions/{session_id}/reject", response_model=LearningSessionResponse)
def reject(session_id: str, request: ReasonRequest, db: Session = Depends(get_db),
           actor: Actor = Depends(current_actor)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.reject(db, session_id, reason=request.reason, rejected_by=acting_as(request.by, actor))))


@router.post("/learning/sessions/{session_id}/activate", response_model=LearningSessionResponse)
def activate(session_id: str, request: ActivateRequest, db: Session = Depends(get_db),
             actor: Actor = Depends(current_actor)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.activate(db, session_id, activated_by=acting_as(request.activated_by, actor))))


@router.post("/learning/sessions/{session_id}/rollback", response_model=LearningSessionResponse)
def rollback(session_id: str, request: RollbackRequest, db: Session = Depends(get_db),
             actor: Actor = Depends(current_actor)) -> LearningSessionResponse:
    return _response(db, _call(lambda: svc.rollback(db, session_id, reason=request.reason, requested_by=acting_as(request.requested_by, actor))))
