"""Governance APIs: configuration, review SLAs, audit log and RBAC policy."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.governance import roles
from app.governance.deps import require
from app.governance.policy import RULES
from app.schema.trust import ConfigUpdateRequest
from app.services import audit_service, monitor_service, scheduler, sla_service
from app.services.auth_service import Actor

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/config")
def get_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    return {
        "review_sla": sla_service.policy(db),
        "alert_thresholds": monitor_service.thresholds(db),
        "static": {  # environment configuration (read-only here; secrets are never returned)
            "rbac_mode": s.rbac_mode,
            "extension_inline_max_bytes": s.extension_inline_max_bytes,
            "extension_inline_max_fields": s.extension_inline_max_fields,
            "overflow_evidence_min_occurrences": s.overflow_evidence_min_occurrences,
            "raw_vault_enabled": s.raw_vault_enabled, "raw_vault_backend": s.raw_vault_backend,
            "merkle_batch_max_events": s.merkle_batch_max_events,
            "merkle_seal_grace_seconds": s.merkle_seal_grace_seconds,
            "export_max_events": s.export_max_events, "export_json_max_events": s.export_json_max_events,
            "scheduler": scheduler.status(),
        },
    }


@router.put("/config")
def put_config(request: ConfigUpdateRequest, actor: Actor = Depends(require(roles.GOVERNANCE)),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    before = {"review_sla": sla_service.policy(db), "alert_thresholds": monitor_service.thresholds(db)}
    try:
        if request.review_sla is not None:
            sla_service.set_policy(db, request.review_sla, by=actor.username)
        if request.alert_thresholds is not None:
            monitor_service.set_thresholds(db, request.alert_thresholds, by=actor.username)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    after = {"review_sla": sla_service.policy(db), "alert_thresholds": monitor_service.thresholds(db)}
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="CONFIG_CHANGE",
                         object_type="governance_settings", object_id=",".join(request.model_dump(exclude_none=True)),
                         details={"before": before, "after": after}, commit=False)
    db.commit()
    return get_config(db)


@router.get("/reviews")
def reviews(
    item_type: Literal["DRIFT_EVENT", "ONBOARDING_SESSION", "LEARNING_SESSION"] | None = None,
    item_id: str | None = Query(default=None, max_length=26),
    include_resolved: bool = False,
    refresh: bool = True,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Review SLA items. refresh=true runs the (idempotent) SLA sweep first so
    statuses are current even between scheduler ticks. A sweep never
    approves, activates or rejects anything."""
    if refresh:
        sla_service.sweep(db)
    items = sla_service.list_rows(db, item_type=item_type, item_id=item_id, include_resolved=include_resolved, limit=limit)
    return {"items": items, "stats": sla_service.stats(db), "policy": sla_service.policy(db)}


@router.get("/audit")
def audit(
    limit: int = Query(default=100, ge=1, le=500),
    before_seq: int | None = Query(default=None, ge=1),
    action: str | None = Query(default=None, max_length=64),
    object_type: str | None = Query(default=None, max_length=64),
    object_id: str | None = Query(default=None, max_length=128),
    actor: str | None = Query(default=None, max_length=128),
    _: Actor = Depends(require(roles.INSPECT)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = audit_service.list_records(db, limit=limit, before_seq=before_seq, action=action,
                                      object_type=object_type, object_id=object_id, actor=actor)
    return {"items": [audit_service.to_dict(r) for r in rows], "stats": audit_service.stats(db),
            "next_before_seq": rows[-1].seq if len(rows) == limit else None}


@router.get("/audit/verify")
def audit_verify(_: Actor = Depends(require(roles.INSPECT)), db: Session = Depends(get_db)) -> dict[str, Any]:
    return audit_service.verify(db)


@router.get("/policy")
def policy() -> dict[str, Any]:
    """The RBAC policy applied to existing governance endpoints (for review/documentation)."""
    return {
        "rbac_mode": get_settings().rbac_mode,
        "roles": {r: roles.capabilities(r) for r in roles.ROLES},
        "rules": [{"method": m, "path": get_settings().api_v1_prefix + p, "action": r.action,
                   "object_type": r.object_type, "critical": r.critical,
                   "maker_checker_actions": list(r.maker_actions)} for (m, p), r in RULES.items()],
    }


@router.post("/scheduler/run")
def run_scheduler(actor: Actor = Depends(require(roles.GOVERNANCE)), db: Session = Depends(get_db)) -> dict[str, Any]:
    result = scheduler.run_once(db)
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="SCHEDULER_RUN",
                         object_type="scheduler", object_id=None, details={"result": result})
    return result
