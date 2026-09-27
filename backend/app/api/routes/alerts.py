"""Internal alert bus API: list, acknowledge, read/unread, channel status."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.alerts.channels import channel_status
from app.api.deps import get_db
from app.governance import roles
from app.governance.deps import require
from app.schema.trust import AckRequest
from app.services import alert_service, audit_service, monitor_service, sla_service
from app.services.auth_service import Actor

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(
    status: Literal["OPEN", "ACKNOWLEDGED"] | None = None,
    kind: str | None = Query(default=None, max_length=48),
    unread: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows, total = alert_service.list_alerts(db, status=status, kind=kind, unread=unread, limit=limit, offset=offset)
    return {"total": total, "items": [alert_service.to_dict(a) for a in rows], "counts": alert_service.counts(db)}


@router.get("/counts")
def counts(db: Session = Depends(get_db)) -> dict[str, Any]:
    return alert_service.counts(db)


@router.get("/channels")
def channels() -> dict[str, Any]:
    return {"channels": channel_status(), "kinds": list(alert_service.KINDS)}


@router.post("/{alert_id}/ack")
def acknowledge(alert_id: str, request: AckRequest, actor: Actor = Depends(require(roles.REVIEW)),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        alert = alert_service.acknowledge(db, alert_id, by=actor.username)
    except alert_service.AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found") from exc
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="ALERT_ACKNOWLEDGE", object_type="alert", object_id=alert_id,
                         details={"kind": alert.kind, "note": request.note})
    return alert_service.to_dict(alert)


@router.post("/{alert_id}/read")
def read(alert_id: str, unread: bool = False, _: Actor = Depends(require(roles.REVIEW)),
         db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return alert_service.to_dict(alert_service.mark_read(db, alert_id, read=not unread))
    except alert_service.AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found") from exc


@router.post("/sweep")
def sweep(actor: Actor = Depends(require(roles.APPROVE_DRIFT)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Run the SLA sweep and every alert condition check now."""
    result = {"review_sla": sla_service.sweep(db), "conditions": monitor_service.sweep(db)}
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="ALERT_SWEEP", object_type="alerts", object_id=None, details=result)
    return {**result, "counts": alert_service.counts(db)}
