"""Phase 8 APIs: compact lineage (Step 3), statistical + semantic drift
(Step 4) and golden baselines (Step 5).

Read endpoints run in READ ONLY transactions. Every mutating endpoint is
audited in the Phase 7 hash-chained audit log (SUCCESS / DENIED / FAILED);
golden baseline changes additionally require an authenticated SOC_ADMIN and
a written note in every RBAC mode. No existing route is changed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.views import get_readonly_db
from app.config import get_settings
from app.governance import roles
from app.governance.policy import actor_from_request
from app.schema.phase8 import (
    FindingAcknowledgeRequest,
    GoldenPinRequest,
    GoldenRepinRequest,
    GoldenRetireRequest,
    StatisticalAnalyzeRequest,
)
from app.services import audit_service
from app.services.auth_service import Actor
from app.services.phase8 import advanced_drift_service as drift_svc
from app.services.phase8 import compact_lineage_service as lineage_svc
from app.services.phase8 import golden_baseline_service as golden_svc

router = APIRouter(tags=["phase8"])


# --------------------------------------------------------------------------
# Governance helpers (audited identity + capability checks)
# --------------------------------------------------------------------------


def _audit(db: Session, actor: Actor, action: str, object_type: str, object_id: str | None, decision: str,
           details: dict[str, Any], evidence_ref: str | None = None) -> None:
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated, action=action,
                         object_type=object_type, object_id=object_id, decision=decision, details=details,
                         evidence_ref=evidence_ref)


def _actor(db: Session, request: Request, action: str, object_type: str, object_id: str | None) -> Actor:
    try:
        return actor_from_request(db, request)
    except HTTPException as exc:
        _audit(db, Actor("invalid-token", None, False), action, object_type, object_id, audit_service.DENIED,
               {"reason": exc.detail})
        raise


def _deny(db: Session, actor: Actor, action: str, object_type: str, object_id: str | None, status: int,
          reason: str, details: dict[str, Any] | None = None) -> HTTPException:
    db.rollback()
    _audit(db, actor, action, object_type, object_id, audit_service.DENIED, {**(details or {}), "reason": reason})
    return HTTPException(status_code=status, detail=reason)


def _operational(db: Session, request: Request, action: str, object_type: str, object_id: str | None,
                 capability: str) -> Actor:
    """Same rule as governance.deps.require for an operational capability, but audited."""
    actor = _actor(db, request, action, object_type, object_id)
    if not actor.authenticated:
        if get_settings().rbac_mode == "enforce":
            raise _deny(db, actor, action, object_type, object_id, 401, "Authentication required (RBAC_MODE=enforce).")
        return actor
    if not roles.has_capability(actor.role, capability):
        raise _deny(db, actor, action, object_type, object_id, 403, f"Role {actor.role} lacks capability '{capability}'.")
    return actor


def _soc_admin_with_note(db: Session, request: Request, action: str, source_key: str, note: str,
                         details: dict[str, Any]) -> Actor:
    actor = _actor(db, request, action, golden_svc.OBJECT_TYPE, source_key)
    if not actor.authenticated:
        raise _deny(db, actor, action, golden_svc.OBJECT_TYPE, source_key, 401,
                    "Golden baselines can only be changed by an authenticated SOC_ADMIN.", details)
    if actor.role != roles.SOC_ADMIN:
        raise _deny(db, actor, action, golden_svc.OBJECT_TYPE, source_key, 403,
                    f"Role {actor.role} cannot change golden baselines (SOC_ADMIN required).", details)
    if not note.strip():
        raise _deny(db, actor, action, golden_svc.OBJECT_TYPE, source_key, 422,
                    "A non-empty note is required to change a golden baseline.", details)
    return actor


# --------------------------------------------------------------------------
# Step 3: compact lineage
# --------------------------------------------------------------------------


@router.get("/lineage/compact/stats")
def compact_lineage_stats(db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    return lineage_svc.stats(db)


@router.get("/lineage/compact/benchmark")
def compact_lineage_benchmark(sample: int = Query(default=50, ge=1, le=500),
                              db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    return lineage_svc.benchmark(db, sample=sample)


@router.get("/lineage/compact/{event_id}")
def compact_lineage(event_id: str, verify: bool = False, db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    try:
        return lineage_svc.get(db, event_id, verify=verify)
    except lineage_svc.LineageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Step 4: statistical + semantic drift
# --------------------------------------------------------------------------


@router.post("/drift/statistical/analyze")
def analyze_statistical_drift(body: StatisticalAnalyzeRequest, request: Request,
                              db: Session = Depends(get_db)) -> dict[str, Any]:
    action = "DRIFT_STATISTICAL_ANALYZE"
    actor = _operational(db, request, action, "drift_findings", body.source_key, roles.REVIEW)
    try:
        report = drift_svc.analyze(db, source_key=body.source_key, window_end=body.window_end,
                                   baseline_hours=body.baseline_hours, current_hours=body.current_hours,
                                   extension_fields=body.extension_fields)
    except drift_svc.AnalysisError as exc:
        raise _deny(db, actor, action, "drift_findings", body.source_key, 422, str(exc))
    _audit(db, actor, action, "drift_findings", body.source_key, audit_service.SUCCESS,
           {"request": body.model_dump(mode="json"), "findings": report["findings"], "advisories": report["advisories"],
            "sources_analyzed": report["sources_analyzed"], "window": report["window"]})
    return report


@router.get("/drift/findings")
def list_drift_findings(layer: Literal["STATISTICAL", "SEMANTIC"] | None = None,
                        source_key: str | None = Query(default=None, max_length=128),
                        status: Literal["OPEN", "ACKNOWLEDGED"] | None = None,
                        limit: int = Query(default=100, ge=1, le=500),
                        db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    items = drift_svc.list_findings(db, layer=layer, source_key=source_key, status=status, limit=limit)
    return {"items": items, "stats": drift_svc.stats(db)}


@router.get("/drift/findings/{finding_id}")
def get_drift_finding(finding_id: str, db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    try:
        return drift_svc.get_finding(db, finding_id)
    except drift_svc.FindingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/drift/findings/{finding_id}/acknowledge")
def acknowledge_drift_finding(finding_id: str, body: FindingAcknowledgeRequest, request: Request,
                              db: Session = Depends(get_db)) -> dict[str, Any]:
    action = "DRIFT_FINDING_ACKNOWLEDGE"
    actor = _operational(db, request, action, "drift_finding", finding_id, roles.REVIEW)
    try:
        drift_svc.acknowledge(db, finding_id, by=actor.username)
    except drift_svc.FindingNotFound as exc:
        raise _deny(db, actor, action, "drift_finding", finding_id, 404, str(exc))
    db.commit()
    _audit(db, actor, action, "drift_finding", finding_id, audit_service.SUCCESS, {"note": body.note},
           f"drift_finding:{finding_id}")
    return drift_svc.get_finding(db, finding_id)


# --------------------------------------------------------------------------
# Step 5: golden baselines
# --------------------------------------------------------------------------


@router.get("/golden-baselines")
def list_golden_baselines(db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    items = golden_svc.list_active(db)
    return {"items": items, "total": len(items),
            "policy": {"golden_similarity_threshold": golden_svc.GOLDEN_SIMILARITY_THRESHOLD,
                       "max_changes_since_golden": golden_svc.MAX_CHANGES_SINCE_GOLDEN,
                       "guarded_actions": list(golden_svc.GUARDED_ACTIONS),
                       "elevated_requires": "authenticated SOC_ADMIN + non-empty note (+ Phase 7 maker-checker)"}}


@router.get("/golden-baselines/comparisons")
def golden_comparisons(source_key: str | None = Query(default=None, max_length=128),
                       limit: int = Query(default=100, ge=1, le=500),
                       db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    return {"items": golden_svc.comparisons(db, source_key=source_key, limit=limit)}


@router.get("/golden-baselines/{source_key}")
def get_golden_baseline(source_key: str, db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    try:
        return golden_svc.detail(db, source_key)
    except golden_svc.GoldenError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("/golden-baselines/{source_key}/compare")
def compare_with_golden(source_key: str, hours: int = Query(default=168, ge=1, le=24 * 90),
                        window_end: datetime | None = None, db: Session = Depends(get_readonly_db)) -> dict[str, Any]:
    try:
        return golden_svc.compare_current(db, source_key, window_end=window_end, hours=hours)
    except golden_svc.GoldenError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


def _golden_change(db: Session, request: Request, action: str, source_key: str, note: str, details: dict[str, Any],
                   change) -> dict[str, Any]:
    actor = _soc_admin_with_note(db, request, action, source_key, note, details)
    try:
        result = change(actor)
    except golden_svc.GoldenError as exc:
        raise _deny(db, actor, action, golden_svc.OBJECT_TYPE, source_key, exc.status, exc.message, details)
    except Exception as exc:
        db.rollback()
        _audit(db, actor, action, golden_svc.OBJECT_TYPE, source_key, audit_service.FAILED,
               {**details, "error": type(exc).__name__})
        raise
    db.commit()
    _audit(db, actor, action, golden_svc.OBJECT_TYPE, source_key, audit_service.SUCCESS,
           {**details, **result["audit"]}, f"golden_baseline:{result['golden']['id']}")
    return result["golden"]


@router.post("/golden-baselines/{source_key}", status_code=201)
def pin_golden_baseline(source_key: str, body: GoldenPinRequest, request: Request,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    def change(actor):
        g = golden_svc.pin(db, source_key, actor=actor, note=body.note.strip(), profile_hours=body.profile_hours,
                           window_end=body.window_end)
        return {"golden": golden_svc.golden_dict(g),
                "audit": {"after": {"id": g.id, "version": g.version, "baseline_version": g.derived_from_baseline_version}}}

    return _golden_change(db, request, "GOLDEN_BASELINE_PIN", source_key, body.note,
                          {"request": body.model_dump(mode="json")}, change)


@router.put("/golden-baselines/{source_key}")
def repin_golden_baseline(source_key: str, body: GoldenRepinRequest, request: Request,
                          db: Session = Depends(get_db)) -> dict[str, Any]:
    def change(actor):
        old, new = golden_svc.repin(db, source_key, actor=actor, note=body.note.strip(),
                                    expected_version=body.expected_version, profile_hours=body.profile_hours,
                                    window_end=body.window_end)
        return {"golden": golden_svc.golden_dict(new),
                "audit": {"before": {"id": old.id, "version": old.version, "status_now": old.status},
                          "after": {"id": new.id, "version": new.version, "baseline_version": new.derived_from_baseline_version}}}

    return _golden_change(db, request, "GOLDEN_BASELINE_REPIN", source_key, body.note,
                          {"request": body.model_dump(mode="json")}, change)


@router.post("/golden-baselines/{source_key}/retire")
def retire_golden_baseline(source_key: str, body: GoldenRetireRequest, request: Request,
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    def change(actor):
        g = golden_svc.retire(db, source_key, expected_version=body.expected_version)
        return {"golden": golden_svc.golden_dict(g), "audit": {"retired": {"id": g.id, "version": g.version}}}

    return _golden_change(db, request, "GOLDEN_BASELINE_RETIRE", source_key, body.note,
                          {"request": body.model_dump(mode="json")}, change)
