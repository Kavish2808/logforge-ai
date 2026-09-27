"""Confidence evidence ledger API."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schema.trust import ULID_PATTERN
from app.services import confidence_service as svc

router = APIRouter(prefix="/confidence", tags=["confidence"])
SubjectId = Path(..., pattern=ULID_PATTERN)


def _get(db: Session, subject_type: str, subject_id: str) -> dict[str, Any]:
    try:
        entries = svc.for_subject(db, subject_type, subject_id)
    except svc.ConfidenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"subject_type": subject_type, "subject_id": subject_id, "entries": entries,
            "note": "Deterministic evidence behind the stated confidence; not a statistical calibration. "
                    "Approval gates are unchanged."}


@router.get("/onboarding/{session_id}")
def onboarding(session_id: str = SubjectId, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _get(db, svc.ONBOARDING, session_id)


@router.get("/learning/{session_id}")
def learning(session_id: str = SubjectId, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _get(db, svc.LEARNING, session_id)


@router.get("/calibration")
def calibration(db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.calibration(db)
