"""Phase 8 Step 5: GOLDEN + CURRENT dual baseline and poisoning protection.

CURRENT baseline = the existing Phase 5 `source_baselines` row. It keeps
evolving exactly as before (drift review, Phase 6 learning); nothing here
changes that workflow or its state machines.

GOLDEN baseline = an explicitly pinned, versioned snapshot of a source's
current baseline (reference + accepted variants, adapter/version context and
a bounded statistical profile). It is created, re-pinned or retired only by
an authenticated SOC_ADMIN with a written note, every attempt is audited,
and no other code path writes it: drift and learning actions never touch it.

Poisoning guard (registered through the Phase 7 guard hook) on
DRIFT_ADD_VARIANT, DRIFT_REPLACE_BASELINE, LEARNING_ACTIVATE and
LEARNING_APPROVE with activate=true (an activation by another route):

    ELEVATED  if  similarity(new structure, golden) < 0.70
              or  more than 5 accepted baseline changes since the golden

ELEVATED is enforced by the Phase 7 policy: authenticated SOC_ADMIN + a
non-empty note, after RBAC and maker-checker. With no golden baseline the
guard stays silent (no block, nothing invented, identical Phase 7 audit).
Every evaluation with a golden stores a `baseline_comparisons` evidence row
whose id is referenced from the audit record.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.governance import AuditLog
from app.db.models.learning import LearningSession
from app.db.models.phase8 import GOLDEN_ACTIVE, GOLDEN_RETIRED, GOLDEN_SUPERSEDED, BaselineComparison, GoldenBaseline
from app.db.models.source_baseline import SourceBaseline, SourceBaselineHistory
from app.governance.policy import GuardContext, GuardDecision
from app.phase8 import drift_stats as ds
from app.pipeline.drift.comparator import compare
from app.services import audit_service
from app.services.phase8 import advanced_drift_service

GOLDEN_SIMILARITY_THRESHOLD = 0.70
MAX_CHANGES_SINCE_GOLDEN = 5
ACCEPTED_CHANGE_ACTIONS = ("VARIANT_ADDED", "BASELINE_REPLACED", "BASELINE_LEARNED")
GUARDED_ACTIONS = ("DRIFT_ADD_VARIANT", "DRIFT_REPLACE_BASELINE", "LEARNING_ACTIVATE", "LEARNING_APPROVE")
GUARD_NAME = "golden_poisoning"
OBJECT_TYPE = "golden_baseline"
REASON_SIMILARITY = "GOLDEN_SIMILARITY_BELOW_THRESHOLD"
REASON_CHANGES = "TOO_MANY_CHANGES_SINCE_GOLDEN"
DECISION_ALLOWED = "ALLOWED"
DECISION_ELEVATED = "ELEVATED"


class GoldenError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------
# Structural helpers
# --------------------------------------------------------------------------


def _snapshot(baseline: SourceBaseline) -> dict[str, Any]:
    return {"reference": baseline.fingerprint, "variants": [v["fingerprint"] for v in baseline.accepted_variants or []],
            "format_detected": baseline.format_detected, "baseline_version": baseline.version,
            "baseline_origin": baseline.origin}


def similarity_to(fingerprint: dict[str, Any] | None, snapshot: dict[str, Any], threshold: float) -> dict[str, Any] | None:
    """Best Phase 5 comparator similarity of `fingerprint` against a snapshot's
    reference and variants (the same deterministic scoring Phase 5 uses)."""
    if not fingerprint or not snapshot.get("reference"):
        return None
    c = compare(fingerprint, snapshot["reference"], snapshot.get("variants") or [], threshold=threshold)
    return {"similarity": c.similarity, "matched": c.matched, "components": c.components,
            "added_fields": c.differences.get("added_fields"), "removed_fields": c.differences.get("removed_fields"),
            "type_changes": c.differences.get("type_changes")}


def active_golden(db: Session, source_key: str) -> GoldenBaseline | None:
    return db.execute(select(GoldenBaseline).where(GoldenBaseline.source_key == source_key,
                                                   GoldenBaseline.status == GOLDEN_ACTIVE)).scalars().first()


def changes_since(db: Session, golden: GoldenBaseline) -> list[SourceBaselineHistory]:
    since = golden.derived_from_baseline_version or 0
    return list(db.execute(
        select(SourceBaselineHistory).where(SourceBaselineHistory.source_key == golden.source_key,
                                            SourceBaselineHistory.version > since,
                                            SourceBaselineHistory.action.in_(ACCEPTED_CHANGE_ACTIONS))
        .order_by(SourceBaselineHistory.version, SourceBaselineHistory.id)).scalars().all())


def golden_dict(g: GoldenBaseline) -> dict[str, Any]:
    return {"id": g.id, "source_key": g.source_key, "version": g.version, "status": g.status,
            "fingerprint": g.fingerprint, "statistical_profile": g.statistical_profile,
            "derived_from_baseline_version": g.derived_from_baseline_version, "approved_by": g.approved_by,
            "approved_role": g.approved_role, "note": g.note, "evidence": g.evidence, "created_at": g.created_at}


# --------------------------------------------------------------------------
# Governance-only mutations
# --------------------------------------------------------------------------


def _require_baseline(db: Session, source_key: str) -> SourceBaseline:
    baseline = db.get(SourceBaseline, source_key)
    if baseline is None:
        raise GoldenError(409, f"Source '{source_key}' has no Phase 5 baseline to pin; a golden baseline is never invented.")
    return baseline


def _build(db: Session, source_key: str, version: int, actor, note: str, profile_hours: int,
           window_end: datetime | None) -> GoldenBaseline:
    baseline = _require_baseline(db, source_key)
    profile = advanced_drift_service.profile_source(db, source_key, window_end=window_end, hours=profile_hours)
    return GoldenBaseline(
        id=generate_event_id(), source_key=source_key, version=version, fingerprint=_snapshot(baseline),
        statistical_profile=profile, derived_from_baseline_version=baseline.version, status=GOLDEN_ACTIVE,
        approved_by=actor.username, approved_role=actor.role, note=note,
        evidence={"adapter_id": baseline.adapter_id, "adapter_version": baseline.adapter_version,
                  "baseline_version": baseline.version, "baseline_origin": baseline.origin,
                  "baseline_updated_at": baseline.updated_at.isoformat() if baseline.updated_at else None,
                  "profile_window": profile["window"], "profile_events": profile["n"],
                  "profile_sufficient": profile["sufficient"], "pinned_at": datetime.now(tz=timezone.utc).isoformat()},
    )


def pin(db: Session, source_key: str, *, actor, note: str, profile_hours: int = 168,
        window_end: datetime | None = None) -> GoldenBaseline:
    if active_golden(db, source_key) is not None:
        raise GoldenError(409, f"Source '{source_key}' already has an active golden baseline; re-pin it with PUT.")
    latest = db.execute(select(func.max(GoldenBaseline.version)).where(GoldenBaseline.source_key == source_key)).scalar()
    golden = _build(db, source_key, (latest or 0) + 1, actor, note, profile_hours, window_end)
    db.add(golden)
    db.flush()
    return golden


def repin(db: Session, source_key: str, *, actor, note: str, expected_version: int | None = None,
          profile_hours: int = 168, window_end: datetime | None = None) -> tuple[GoldenBaseline, GoldenBaseline]:
    current = active_golden(db, source_key)
    if current is None:
        raise GoldenError(409, f"Source '{source_key}' has no active golden baseline; pin one with POST first.")
    if expected_version is not None and expected_version != current.version:
        raise GoldenError(409, f"Golden baseline is at v{current.version}, not v{expected_version}; reload and retry.")
    makers = change_makers(db, current)
    if actor.username in makers:
        raise GoldenError(403, f"Maker-checker: '{actor.username}' approved baseline changes since golden "
                               f"v{current.version} and cannot also bless them as the new golden baseline.")
    new = _build(db, source_key, current.version + 1, actor, note, profile_hours, window_end)
    current.status = GOLDEN_SUPERSEDED
    db.flush()  # the partial unique index allows one ACTIVE row per source
    new.evidence = {**new.evidence, "supersedes": {"id": current.id, "version": current.version},
                    "changes_since_previous": len(changes_since(db, current))}
    db.add(new)
    db.flush()
    return current, new


def retire(db: Session, source_key: str, *, expected_version: int | None = None) -> GoldenBaseline:
    current = active_golden(db, source_key)
    if current is None:
        raise GoldenError(404, f"Source '{source_key}' has no active golden baseline.")
    if expected_version is not None and expected_version != current.version:
        raise GoldenError(409, f"Golden baseline is at v{current.version}, not v{expected_version}; reload and retry.")
    current.status = GOLDEN_RETIRED
    db.flush()
    return current


def change_makers(db: Session, golden: GoldenBaseline) -> set[str]:
    """Authenticated actors who approved accepted baseline changes since `golden`."""
    changes = changes_since(db, golden)
    event_ids = [c.event_id for c in changes if c.action != "BASELINE_LEARNED" and c.event_id]
    makers: set[str] = set()
    if event_ids:
        makers |= set(db.execute(select(AuditLog.actor).where(
            AuditLog.action.in_(("DRIFT_ADD_VARIANT", "DRIFT_REPLACE_BASELINE")), AuditLog.object_type == "event",
            AuditLog.object_id.in_(event_ids), AuditLog.decision == audit_service.SUCCESS,
            AuditLog.authenticated.is_(True))).scalars().all())
    if any(c.action == "BASELINE_LEARNED" for c in changes):
        sessions = db.execute(select(LearningSession.id).where(LearningSession.source_key == golden.source_key,
                                                               LearningSession.activated_at >= golden.created_at)).scalars().all()
        if sessions:
            makers |= set(db.execute(select(AuditLog.actor).where(
                AuditLog.action.in_(("LEARNING_ACTIVATE", "LEARNING_APPROVE")), AuditLog.object_id.in_(list(sessions)),
                AuditLog.decision == audit_service.SUCCESS, AuditLog.authenticated.is_(True))).scalars().all())
    return makers


# --------------------------------------------------------------------------
# Poisoning assessment + guard
# --------------------------------------------------------------------------


def assess(db: Session, source_key: str, new_fingerprint: dict[str, Any] | None, *, action: str,
           object_type: str, object_id: str | None, persist: bool = True) -> dict[str, Any] | None:
    """None when the source has no golden baseline (compatible behavior)."""
    golden = active_golden(db, source_key)
    if golden is None:
        return None
    from app.config import get_settings

    current = db.get(SourceBaseline, source_key)
    current_snapshot = _snapshot(current) if current is not None else None
    new_vs_golden = similarity_to(new_fingerprint, golden.fingerprint, GOLDEN_SIMILARITY_THRESHOLD)
    new_vs_current = (similarity_to(new_fingerprint, current_snapshot, get_settings().drift_similarity_threshold)
                      if current_snapshot else None)
    current_vs_golden = (similarity_to(current_snapshot["reference"], golden.fingerprint, GOLDEN_SIMILARITY_THRESHOLD)
                         if current_snapshot else None)
    changes = changes_since(db, golden)
    reasons: list[dict[str, Any]] = []
    if new_vs_golden is not None and new_vs_golden["similarity"] < GOLDEN_SIMILARITY_THRESHOLD:
        reasons.append({"code": REASON_SIMILARITY, "value": new_vs_golden["similarity"],
                        "threshold": GOLDEN_SIMILARITY_THRESHOLD,
                        "detail": f"The proposed structure is {new_vs_golden['similarity']:.4f} similar to golden "
                                  f"v{golden.version} (below {GOLDEN_SIMILARITY_THRESHOLD})."})
    if len(changes) > MAX_CHANGES_SINCE_GOLDEN:
        reasons.append({"code": REASON_CHANGES, "value": len(changes), "threshold": MAX_CHANGES_SINCE_GOLDEN,
                        "detail": f"{len(changes)} accepted baseline changes since golden v{golden.version} "
                                  f"(more than {MAX_CHANGES_SINCE_GOLDEN})."})
    decision = DECISION_ELEVATED if reasons else DECISION_ALLOWED
    now = datetime.now(tz=timezone.utc)
    comparison_id = hashlib.sha256(
        f"{source_key}|{action}|{object_type}|{object_id}|{golden.id}|{now.isoformat()}".encode()).hexdigest()
    result = {
        "comparison_id": comparison_id, "source_key": source_key, "decision": decision,
        "golden": {"id": golden.id, "version": golden.version,
                   "derived_from_baseline_version": golden.derived_from_baseline_version},
        "current_baseline_version": current.version if current is not None else None,
        "new_vs_current": new_vs_current, "new_vs_golden": new_vs_golden, "current_vs_golden": current_vs_golden,
        "steps_since_golden": len(changes),
        "changes_since_golden": [{"version": c.version, "action": c.action, "event_id": c.event_id} for c in changes],
        "thresholds": {"golden_similarity": GOLDEN_SIMILARITY_THRESHOLD, "max_changes_since_golden": MAX_CHANGES_SINCE_GOLDEN},
        "poisoning_risk": bool(reasons), "reasons": reasons,
        "explanation": (" ".join(r["detail"] for r in reasons) + " Elevated review required."
                        if reasons else
                        f"Within golden v{golden.version} limits: similarity "
                        f"{(new_vs_golden or {}).get('similarity')} >= {GOLDEN_SIMILARITY_THRESHOLD} and "
                        f"{len(changes)} <= {MAX_CHANGES_SINCE_GOLDEN} accepted changes since golden."),
    }
    if persist:
        db.add(BaselineComparison(
            id=comparison_id, source_key=source_key, action=action, object_type=object_type, object_id=object_id,
            new_vs_current=new_vs_current or {}, new_vs_golden=new_vs_golden, current_vs_golden=current_vs_golden,
            steps_since_golden=len(changes), poisoning_risk=bool(reasons), risk_reasons=reasons, decision=decision,
        ))
        db.commit()
    return result


def _target(ctx: GuardContext) -> tuple[str, dict[str, Any] | None] | None:
    """(source_key, proposed structure) of a guarded action, or None when the
    action does not change a baseline (it then fails or no-ops on its own)."""
    if ctx.action in ("DRIFT_ADD_VARIANT", "DRIFT_REPLACE_BASELINE"):
        event = ctx.db.get(Event, ctx.object_id) if ctx.object_id else None
        drift = ((event.processing_metadata or {}).get("drift") if event is not None else None) or {}
        if event is None or event.status != "UNDER_REVIEW" or drift.get("status") != "DRIFT" or not drift.get("source_key"):
            return None
        return drift["source_key"], event.structural_fingerprint
    if ctx.action == "LEARNING_APPROVE" and not ctx.body.get("activate"):
        return None
    session = ctx.db.get(LearningSession, ctx.object_id) if ctx.object_id else None
    if session is None or session.status == "ACTIVE":
        return None
    return session.source_key, session.new_fingerprint


def guard(ctx: GuardContext) -> GuardDecision | None:
    target = _target(ctx)
    if target is None:
        return None
    source_key, fingerprint = target
    result = assess(ctx.db, source_key, fingerprint, action=ctx.action, object_type=ctx.object_type,
                    object_id=ctx.object_id)
    if result is None:
        return None
    evidence = {k: result[k] for k in ("comparison_id", "source_key", "decision", "golden", "steps_since_golden",
                                       "poisoning_risk", "thresholds")}
    evidence["new_vs_golden_similarity"] = (result["new_vs_golden"] or {}).get("similarity")
    evidence["current_vs_golden_similarity"] = (result["current_vs_golden"] or {}).get("similarity")
    evidence["reasons"] = [r["code"] for r in result["reasons"]]
    return GuardDecision(elevated=result["poisoning_risk"], reason=result["explanation"], evidence=evidence)


# --------------------------------------------------------------------------
# Read API
# --------------------------------------------------------------------------


def detail(db: Session, source_key: str) -> dict[str, Any]:
    versions = db.execute(select(GoldenBaseline).where(GoldenBaseline.source_key == source_key)
                          .order_by(GoldenBaseline.version.desc())).scalars().all()
    if not versions:
        raise GoldenError(404, f"Source '{source_key}' has no golden baseline.")
    active = next((g for g in versions if g.status == GOLDEN_ACTIVE), None)
    return {"source_key": source_key, "active": golden_dict(active) if active else None,
            "versions": [golden_dict(g) for g in versions]}


def list_active(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(GoldenBaseline).where(GoldenBaseline.status == GOLDEN_ACTIVE)
                      .order_by(GoldenBaseline.source_key)).scalars().all()
    return [golden_dict(g) for g in rows]


def compare_current(db: Session, source_key: str, *, window_end: datetime | None = None,
                    hours: int = 168) -> dict[str, Any]:
    """Explainable CURRENT-vs-GOLDEN comparison (read-only; stores nothing)."""
    golden = active_golden(db, source_key)
    if golden is None:
        raise GoldenError(404, f"Source '{source_key}' has no active golden baseline.")
    current = db.get(SourceBaseline, source_key)
    snapshot = _snapshot(current) if current is not None else None
    structural = similarity_to(snapshot["reference"], golden.fingerprint, GOLDEN_SIMILARITY_THRESHOLD) if snapshot else None
    changes = changes_since(db, golden)
    profile = advanced_drift_service.profile_source(db, source_key, window_end=window_end, hours=hours)
    gold_profile = golden.statistical_profile or {}
    statistical: dict[str, Any] = {"compared": False}
    if profile["sufficient"] and gold_profile.get("sufficient"):
        per_field = {}
        for f in ds.MONITORED_FIELDS:
            g, c = (gold_profile.get("fields") or {}).get(f) or {}, profile["fields"][f]
            value = ds.psi_from_profiles(g, c)
            per_field[f] = {"psi": value, "psi_threshold": ds.THRESHOLDS["PSI"][0],
                            "psi_exceeded": value is not None and value >= ds.THRESHOLDS["PSI"][0],
                            "golden_null_rate": g.get("null_rate"), "current_null_rate": c.get("null_rate")}
        statistical = {"compared": True, "golden_window": gold_profile.get("window"), "current_window": profile["window"],
                       "golden_events": gold_profile.get("n"), "current_events": profile["n"], "fields": per_field}
    else:
        statistical["reason"] = (f"Statistical comparison needs >= {ds.MIN_SAMPLE} events in both the golden profile "
                                 f"({gold_profile.get('n')}) and the current window ({profile['n']}).")
    reasons = []
    if structural is not None and structural["similarity"] < GOLDEN_SIMILARITY_THRESHOLD:
        reasons.append(REASON_SIMILARITY)
    if len(changes) > MAX_CHANGES_SINCE_GOLDEN:
        reasons.append(REASON_CHANGES)
    explanation = [
        f"Golden v{golden.version} was pinned from Phase 5 baseline v{golden.derived_from_baseline_version} by "
        f"{golden.approved_by}; the current baseline is v{current.version if current else '?'}.",
        f"Current reference structure vs golden: similarity {structural['similarity'] if structural else 'n/a'} "
        f"(threshold {GOLDEN_SIMILARITY_THRESHOLD}).",
        f"{len(changes)} accepted baseline change(s) since the golden (limit {MAX_CHANGES_SINCE_GOLDEN}).",
    ]
    if statistical.get("compared"):
        exceeded = [f for f, v in statistical["fields"].items() if v["psi_exceeded"]]
        explanation.append(f"Statistical profile: PSI above {ds.THRESHOLDS['PSI'][0]} on {exceeded or 'no field'}.")
    else:
        explanation.append(statistical["reason"])
    return {"source_key": source_key, "golden": {"id": golden.id, "version": golden.version},
            "current_baseline_version": current.version if current else None, "structural": structural,
            "steps_since_golden": len(changes),
            "changes_since_golden": [{"version": c.version, "action": c.action, "event_id": c.event_id,
                                      "note": c.note, "created_at": c.created_at} for c in changes],
            "statistical": statistical, "next_change_would_be_elevated": bool(reasons), "reasons": reasons,
            "explanation": explanation}


def comparisons(db: Session, *, source_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(BaselineComparison)
    if source_key:
        stmt = stmt.where(BaselineComparison.source_key == source_key)
    rows = db.execute(stmt.order_by(BaselineComparison.created_at.desc(), BaselineComparison.id).limit(limit)).scalars()
    return [{"id": r.id, "source_key": r.source_key, "action": r.action, "object_type": r.object_type,
             "object_id": r.object_id, "decision": r.decision, "poisoning_risk": r.poisoning_risk,
             "risk_reasons": r.risk_reasons, "steps_since_golden": r.steps_since_golden,
             "new_vs_current": r.new_vs_current, "new_vs_golden": r.new_vs_golden,
             "current_vs_golden": r.current_vs_golden, "created_at": r.created_at} for r in rows]
