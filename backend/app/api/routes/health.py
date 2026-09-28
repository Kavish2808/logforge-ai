from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/health/live")
@router.get("/api/health/live")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "live": True}


@router.get("/health/readiness")
@router.get("/api/health/readiness")
def readiness(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ready", "ready": True}
    except Exception:
        return {"status": "unavailable", "database": "unavailable", "ready": False}

