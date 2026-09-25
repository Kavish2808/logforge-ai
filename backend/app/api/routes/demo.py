"""Demo Mode API (/api/v1/demo).

Only fixtures, a read-only progress status and a scoped reset live here.
The demo's state transitions are made through the normal public endpoints
(ingest, onboarding, drift, learning) by the client that runs the demo."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.views import get_readonly_db
from app.demo import fixtures as fx
from app.services import demo_service

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/fixtures")
def get_fixtures() -> dict[str, Any]:
    return {
        "namespace": {"session_name": fx.DEMO_ID, "source_key": fx.DEMO_SOURCE_KEY, "marker": fx.DEMO_MARKER,
                      "operator": fx.DEMO_OPERATOR},
        "fixtures": [{"key": f.key, "kind": f.kind, "raw": f.raw, "sha256": f.sha256, "purpose": f.purpose}
                     for f in fx.FIXTURES],
    }


@router.get("/status")
def get_status(db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    return demo_service.status(db)


@router.post("/reset")
def post_reset(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return demo_service.reset(db)
    except demo_service.DemoConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
