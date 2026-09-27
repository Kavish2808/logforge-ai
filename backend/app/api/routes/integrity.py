"""Integrity APIs: extension overflow, cold raw vault, Merkle evidence chain."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.event import Event
from app.db.models.evidence import OverflowSignature
from app.governance import roles
from app.governance.deps import require
from app.schema.trust import ULID_PATTERN, SealRequest
from app.services import audit_service, evidence_service, onboarding_service
from app.services.auth_service import Actor

router = APIRouter(prefix="/integrity", tags=["integrity"])
EventId = Path(..., pattern=ULID_PATTERN)


def _nf(fn):
    try:
        return fn()
    except evidence_service.EvidenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"merkle": evidence_service.integrity_stats(db), "raw_vault": evidence_service.vault_stats(db),
            "extension_overflow": evidence_service.overflow_stats(db)}


# --- Merkle evidence chain -------------------------------------------------------------------


@router.post("/seal")
def seal(request: SealRequest, actor: Actor = Depends(require(roles.APPROVE_DRIFT)),
         db: Session = Depends(get_db)) -> dict[str, Any]:
    batches = evidence_service.seal(db, force=request.force)
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="INTEGRITY_SEAL", object_type="evidence_chain",
                         object_id=str(batches[-1].seq) if batches else None,
                         details={"force": request.force, "batches": [b.seq for b in batches],
                                  "events": sum(b.event_count for b in batches)})
    return {"sealed_batches": [evidence_service.anchor_record(b) for b in batches],
            "status": evidence_service.integrity_stats(db)}


@router.get("/verify")
def verify_chain(db: Session = Depends(get_db)) -> dict[str, Any]:
    return evidence_service.verify_chain(db)


@router.get("/batches")
def batches(limit: int = Query(default=50, ge=1, le=500), before_seq: int | None = Query(default=None, ge=1),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": evidence_service.list_batches(db, limit=limit, before_seq=before_seq)}


@router.get("/events/{event_id}")
def verify_event(event_id: str = EventId, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _nf(lambda: evidence_service.verify_event(db, event_id))


# --- Raw vault ---------------------------------------------------------------------------------


@router.get("/raw/{event_id}")
def raw_status(event_id: str = EventId, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _nf(lambda: evidence_service.raw_status(db, event_id))


@router.get("/raw/{event_id}/recover")
def recover_raw(event_id: str = EventId, actor: Actor = Depends(require(roles.INSPECT)),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    """Read the raw payload back from the cold vault and prove byte equality
    with the event's SHA-256 (and with the hot copy)."""
    result = _nf(lambda: evidence_service.recover_raw(db, event_id, record_verification=True))
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="RAW_RECOVER", object_type="event", object_id=event_id,
                         decision=audit_service.SUCCESS if result.get("recovered") else audit_service.FAILED,
                         details={k: v for k, v in result.items() if k != "raw_event"})
    return result


@router.post("/raw/backfill")
def backfill(limit: int = Query(default=500, ge=1, le=10_000), actor: Actor = Depends(require(roles.APPROVE_DRIFT)),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    result = evidence_service.backfill_vault(db, limit=limit)
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="RAW_VAULT_BACKFILL", object_type="raw_vault", object_id=None, details=result)
    return {**result, "vault": evidence_service.vault_stats(db)}


# --- Extension overflow --------------------------------------------------------------------------


@router.get("/extensions/{event_id}")
def extensions(event_id: str = EventId, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The event's complete extension set (inline + overflow), with integrity."""
    return _nf(lambda: evidence_service.extension_view(db, event_id))


@router.get("/overflow/stats")
def overflow_stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return evidence_service.overflow_stats(db)


@router.get("/overflow/evidence")
def overflow_evidence(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": evidence_service.overflow_evidence(db, limit=limit)}


@router.post("/overflow/evidence/{signature_id}/onboarding", status_code=201)
def overflow_to_onboarding(signature_id: int, actor: Actor = Depends(require(roles.PROPOSE)),
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn a repeated overflow structure into an onboarding session whose
    samples are the stored events (normal Phase 3 gates apply from here)."""
    sig = db.get(OverflowSignature, signature_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"Overflow signature {signature_id} not found")
    if not evidence_service.signature_dict(sig)["onboarding_evidence"]:
        raise HTTPException(status_code=409, detail="This structure has not reached the onboarding-evidence threshold yet.")
    if sig.onboarding_session_id:
        raise HTTPException(status_code=409, detail=f"Already sent to onboarding session {sig.onboarding_session_id}.")
    ids = [i for i in sig.sample_event_ids if db.get(Event, i) is not None]
    if not ids:
        raise HTTPException(status_code=409, detail="None of the sample events still exist.")
    try:
        session = onboarding_service.create_session(db, samples=[], event_ids=ids,
                                                    name=f"overflow:{sig.adapter_id}:{sig.key_signature[:12]}")
    except onboarding_service.OnboardingError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    sig.onboarding_session_id = session.id
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="OVERFLOW_TO_ONBOARDING", object_type="onboarding_session", object_id=session.id,
                         details={"signature_id": sig.id, "adapter_id": sig.adapter_id, "samples": len(ids)},
                         commit=False)
    db.commit()
    return {"onboarding_session_id": session.id, "samples": len(ids), "signature": evidence_service.signature_dict(sig)}
