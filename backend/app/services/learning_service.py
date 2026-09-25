"""Phase 6 — continuous adaptive learning workflow.

    Phase 5 drift  ->  human accepts it (add_variant / replace_baseline)
    ->  POST /events/{id}/learning/propose     (explicit human learning action)
    ->  evidence: drifted + historical stored events (ids + SHA-256)
    ->  deterministic minimal delta (+ optional, untrusted LLM suggestions)
    ->  sandbox on drifted AND historical events (real runtime)
    ->  PASSED / NEEDS_REVIEW / FAILED (or NO_CHANGE_REQUIRED)
    ->  human APPROVE  ->  explicit ACTIVATE  ->  new onboarded adapter version
    ->  Phase 5 baseline adopts the learned structure (old one kept as a variant)
    ->  future logs use the new version; Phase 5 keeps monitoring.

Invariants:
- Only human-accepted drift of an *onboarded* adapter is learnable (shipped
  YAML adapters are frozen); acknowledged / unreviewed / format drift is not.
- Nothing changes production until explicit approval AND activation; the LLM
  can only add LOW-confidence suggestions that pass every gate.
- Approvals/activations lock the session row; the Phase 3 partial unique index
  guarantees one ACTIVE version per adapter; activation is idempotent.
- Identical mappings never create a version; failed validation never does.
- Every action is appended to the session's decision history.
- Learning never runs on the ingestion path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.loader import get_adapter_registry
from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.learning import (
    ACTIVE,
    APPROVED,
    FAILED,
    NEEDS_REVIEW,
    NO_CHANGE_REQUIRED,
    PROPOSED,
    REJECTED,
    ROLLED_BACK,
    VALIDATED,
    LearningSession,
)
from app.db.models.onboarding import ADAPTER_ACTIVE, ADAPTER_SUPERSEDED, OnboardedAdapter
from app.db.models.source_baseline import ORIGIN_HUMAN_REVIEW
from app.db.repository import baseline_repo, event_repo, learning_repo, onboarding_repo
from app.learning import validation as learning_validation
from app.learning.assistant import accept_suggestions, get_assistant, parse_suggestions
from app.learning.delta import (
    PRESERVED_IN_EXTENSIONS,
    LearningDelta,
    apply_delta,
    current_mappings,
    mapping_diff,
    normalized_mapping,
    review_delta,
)
from app.learning.engine import build_delta, field_evidence
from app.learning.report import recommendation, render_report
from app.onboarding.providers import SuggestionError
from app.pipeline.drift import analysis as drift_analysis
from app.pipeline.drift.comparator import structural_differences
from app.schema.adapter import AdapterMapping
from app.schema.ocsf import DriftStatus
from app.services import onboarding_service

logger = logging.getLogger(__name__)

LEARNABLE_RESOLUTIONS = ("accepted_variant", "replaced_baseline")
HISTORY_BASELINE_LEARNED = "BASELINE_LEARNED"
MAX_BASELINE_VARIANTS = 50


class LearningError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LearningNotFound(LearningError):
    status_code = 404


class LearningConflict(LearningError):
    status_code = 409


class LearningUnprocessable(LearningError):
    status_code = 422


# --------------------------------------------------------------------------
# Propose
# --------------------------------------------------------------------------


def propose(db: Session, event_id: str, *, assistant: str, requested_by: str | None) -> LearningSession:
    event = event_repo.get_event(db, event_id)
    if event is None:
        raise LearningNotFound(f"Event '{event_id}' not found")
    drift = (event.processing_metadata or {}).get("drift")
    _check_eligible(event, drift)

    source_key = drift["source_key"]
    source_row = next((v for v in onboarding_repo.adapter_versions(db, source_key) if v.status == ADAPTER_ACTIVE), None)
    if source_row is None:
        shipped = source_key in {a.id for a in get_adapter_registry().all()}
        raise LearningConflict(
            f"Source '{source_key}' is a shipped adapter; shipped adapters are frozen and are not evolved by learning."
            if shipped else f"Source '{source_key}' has no active onboarded adapter version to evolve."
        )
    if learning_repo.open_session_for_trigger(db, event_id) is not None:
        raise LearningConflict(f"An open learning session already exists for event '{event_id}'.")

    adapter = AdapterMapping.model_validate(source_row.mapping)
    new_fp = event.structural_fingerprint
    # Evidence = stored events with exactly the drifted structure (field order
    # and types), and previously accepted structures of the same source.
    drifted_events = [e for e in learning_repo.events_with_signature(db, source_key, new_fp["signature"])
                      if _same_structure(e.structural_fingerprint, new_fp)]
    if event.event_id not in {e.event_id for e in drifted_events}:
        drifted_events = [event, *drifted_events]
    drifted_events = drifted_events[: learning_repo.MAX_EVIDENCE_EVENTS]
    historical_events = [e for e in learning_repo.processed_events(db, source_key)
                         if e.structural_fingerprint and not _same_structure(e.structural_fingerprint, new_fp)
                         ][: learning_repo.MAX_EVIDENCE_EVENTS]
    baseline = baseline_repo.get_baseline(db, source_key)
    old_fp = _old_fingerprint(baseline, historical_events, new_fp)

    session = LearningSession(
        id=generate_event_id(),
        source_key=source_key,
        source_adapter_id=source_row.adapter_id,
        source_adapter_version=source_row.version,
        source_adapter_row_id=source_row.id,
        trigger_event_id=event.event_id,
        trigger_raw_hash=event.raw_hash,
        status=PROPOSED,
        drift=drift,
        old_fingerprint=old_fp,
        new_fingerprint=new_fp,
        evidence={
            "drifted": [_ref(e) for e in drifted_events],
            "historical": [_ref(e) for e in historical_events],
            "baseline_version": baseline.version if baseline else None,
            "approved_variant_signatures": [v["fingerprint"].get("signature") for v in (baseline.accepted_variants if baseline else [])],
        },
        learning_modes=[],
        risk="LOW",
        risk_reasons=[],
        proposal_version=0,
        decisions=[_decision("PROPOSED", requested_by, note=f"learning requested from drift on event {event_id}")],
    )
    learning_repo.add(db, session)

    result = _learn(adapter, session, [e.raw_event for e in drifted_events], [e.raw_event for e in historical_events])
    delta: LearningDelta = result["delta"]
    session.learning_modes = result["modes"]
    session.risk = result["risk"]
    session.risk_reasons = result["risk_reasons"]
    session.assistant = _assist(assistant, delta, result, [e.raw_event for e in drifted_events], adapter)
    if session.assistant.get("accepted") and session.risk == "LOW":
        session.risk = "MEDIUM"  # assistant suggestions are LOW-confidence by definition
    _set_proposal(session, delta, source="deterministic" + (f"+{session.assistant['name']}" if session.assistant.get("accepted") else ""))
    _validate(db, session)
    db.commit()
    db.refresh(session)
    return session


def _check_eligible(event, drift) -> None:
    if not isinstance(drift, dict) or drift.get("status") != DriftStatus.DRIFT.value:
        raise LearningConflict("Only structural DRIFT events can be learned (possible format drift, normal events and errors cannot).")
    resolution = (drift.get("review") or {}).get("resolution")
    if resolution not in LEARNABLE_RESOLUTIONS:
        state = "not been reviewed yet" if resolution is None else f"been '{resolution}', not accepted"
        raise LearningConflict(
            f"This drift has {state}. Only drift a human accepted as legitimate (add_variant / replace_baseline) "
            "can be learned."
        )


def _learn(adapter, session, drifted_raws, historical_raws) -> dict[str, Any]:
    drifted, drifted_n = field_evidence(adapter, drifted_raws)
    historical, _ = field_evidence(adapter, historical_raws)
    critical = drift_analysis.resolve_critical_fields(adapter, get_settings().drift_critical_fields_list)
    differences = session.drift.get("differences") or structural_differences(session.new_fingerprint, session.old_fingerprint or {})
    result = build_delta(adapter, differences, session.drift.get("change_types") or [], drifted, drifted_n, historical, critical)
    result["drifted_fields"] = drifted
    result["drifted_count"] = drifted_n
    return result


def _assist(choice: str, delta: LearningDelta, result: dict[str, Any], drifted_raws: list[str], adapter) -> dict[str, Any]:
    """Optional LLM suggestions for unresolved added fields. Never fatal:
    any failure leaves the deterministic delta unchanged."""
    unresolved = {u.field: result["drifted_fields"].get(u.field, {}) for u in delta.unresolved
                  if u.kind == PRESERVED_IN_EXTENSIONS and u.field in result["drifted_fields"]}
    info: dict[str, Any] = {"name": "offline", "requested": choice, "accepted": [], "rejected": [], "error": None}
    if not unresolved:
        return info
    try:
        assistant = get_assistant(choice)
        info["name"] = assistant.name
        taken = {m["target"] for m in current_mappings(adapter).values()} | {m.target for m in delta.add_mappings}
        raw = assistant.suggest({"unresolved_fields": unresolved, "taken_targets": sorted(taken), "samples": drifted_raws})
        accepted, rejected = accept_suggestions(parse_suggestions(raw), unresolved, taken, result["drifted_count"])
    except SuggestionError as exc:
        info["error"] = {"kind": exc.kind, "message": exc.message}
        return info
    except Exception as exc:  # noqa: BLE001 — an assistant bug never breaks learning
        logger.exception("Learning assistant failed.")
        info["error"] = {"kind": "UNAVAILABLE", "message": f"Assistant error ({type(exc).__name__})."}
        return info
    fields = {m.raw_field for m in accepted}
    delta.add_mappings.extend(accepted)
    delta.unresolved = [u for u in delta.unresolved if u.field not in fields]
    info["accepted"] = [m.model_dump() for m in accepted]
    info["rejected"] = rejected
    return info


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(db: Session, session_id: str) -> LearningSession:
    session = _get(db, session_id, lock=True)
    _require(session, (PROPOSED, VALIDATED, NEEDS_REVIEW, FAILED, NO_CHANGE_REQUIRED), "re-validate")
    _validate(db, session)
    _append(session, "REVALIDATED", None, note=f"result {session.validation['result']}")
    db.commit()
    db.refresh(session)
    return session


def submit_proposal(db: Session, session_id: str, delta_data: dict[str, Any], submitted_by: str | None) -> LearningSession:
    """A human-written/edited delta goes through exactly the same gates."""
    session = _get(db, session_id, lock=True)
    _require(session, (PROPOSED, VALIDATED, NEEDS_REVIEW, FAILED, NO_CHANGE_REQUIRED), "submit a proposal")
    try:
        delta = LearningDelta.model_validate(delta_data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()[:10])
        raise LearningUnprocessable(f"Proposal failed schema validation: {problems}") from exc
    _set_proposal(session, delta, source="human")
    _validate(db, session)
    _append(session, "PROPOSAL_SUBMITTED", submitted_by, note=f"proposal v{session.proposal_version}: {session.validation['result']}")
    db.commit()
    db.refresh(session)
    return session


def _set_proposal(session: LearningSession, delta: LearningDelta, *, source: str) -> None:
    session.proposal = delta.model_dump(mode="json")
    session.proposal_version += 1
    session.proposal_source = source


def _validate(db: Session, session: LearningSession) -> None:
    """Deterministic: review the delta against the evidence, build the
    candidate version and run the learning sandbox. Sets status."""
    settings = get_settings()
    source_row = db.get(OnboardedAdapter, session.source_adapter_row_id)
    adapter = AdapterMapping.model_validate(source_row.mapping)
    delta = LearningDelta.model_validate(session.proposal)
    drifted_raws = _raws(db, session.evidence["drifted"])
    historical_raws = _raws(db, session.evidence["historical"])
    drifted_fields, _ = field_evidence(adapter, drifted_raws)
    differences = session.drift.get("differences") or {}
    issues = review_delta(delta, adapter, differences, set(drifted_fields))
    if len(drifted_raws) < len(session.evidence["drifted"]):
        issues.append("Some drifted evidence events no longer exist; re-propose from current evidence.")

    if not delta.changes_mapping() and not delta.needs_manual_mapping() and not issues:
        session.candidate = None
        session.mapping_diff = None
        session.target_version = None
        session.validation = {"result": "NO_CHANGE", "reasons": [*delta.notes, "No adapter change is needed; no version will be created."],
                              "validated_at": _now_iso(), "proposal_version": session.proposal_version}
        session.status = NO_CHANGE_REQUIRED
        return

    target_version = max(v.version for v in onboarding_repo.adapter_versions(db, session.source_adapter_id)) + 1
    candidate = None
    if not issues:
        try:
            candidate = apply_delta(adapter, delta, version=target_version,
                                    description=f"Learned by Phase 6 session {session.id} from adapter v{adapter.version}")
        except (ValidationError, ValueError) as exc:
            issues.append(f"The proposal does not form a valid adapter: {str(exc)[:300]}")
    outcome = learning_validation.validate_candidate(
        candidate or adapter,
        onboarding_service.runtime_registry(db),
        drifted_raws,
        historical_raws,
        issues=issues,
        needs_manual=delta.needs_manual_mapping(),
        risk_reasons=session.risk_reasons or [],
        changes_mapping=delta.changes_mapping(),
        min_match_rate=settings.onboarding_min_match_rate,
        reject_below_match_rate=settings.onboarding_reject_below_match_rate,
        min_mapping_coverage=settings.onboarding_min_mapping_coverage,
    )
    session.candidate = candidate.model_dump(mode="json") if candidate else None
    session.mapping_diff = mapping_diff(adapter, candidate) if candidate else None
    session.target_version = target_version if candidate else None
    session.validation = {**outcome, "validated_at": _now_iso(), "proposal_version": session.proposal_version,
                          "thresholds": {"min_match_rate": settings.onboarding_min_match_rate,
                                         "reject_below_match_rate": settings.onboarding_reject_below_match_rate,
                                         "min_mapping_coverage": settings.onboarding_min_mapping_coverage}}
    session.status = {learning_validation.PASSED: VALIDATED, learning_validation.NEEDS_REVIEW: NEEDS_REVIEW}.get(outcome["result"], FAILED)


# --------------------------------------------------------------------------
# Human decisions
# --------------------------------------------------------------------------


def request_review(db: Session, session_id: str, *, reason: str, requested_by: str | None) -> LearningSession:
    session = _get(db, session_id, lock=True)
    _require(session, (PROPOSED, VALIDATED), "request a review")
    session.status = NEEDS_REVIEW
    _append(session, "REVIEW_REQUESTED", requested_by, note=reason)
    db.commit()
    db.refresh(session)
    return session


def reject(db: Session, session_id: str, *, reason: str, rejected_by: str | None) -> LearningSession:
    session = _get(db, session_id, lock=True)
    _require(session, (PROPOSED, VALIDATED, NEEDS_REVIEW, FAILED, APPROVED, NO_CHANGE_REQUIRED), "reject")
    session.status = REJECTED
    session.rejection_reason = reason
    _append(session, "REJECTED", rejected_by, note=reason)
    db.commit()
    db.refresh(session)
    return session


def approve(
    db: Session,
    session_id: str,
    *,
    proposal_version: int,
    approved_by: str | None,
    note: str | None,
    confirm_supersede: bool,
    activate_now: bool,
) -> LearningSession:
    session = _get(db, session_id, lock=True)
    if session.status in (APPROVED, ACTIVE):
        raise LearningConflict(f"Learning session '{session.id}' is already {session.status}.")
    _require(session, (VALIDATED, NEEDS_REVIEW), "approve")
    _ensure_current_base(db, session)
    if proposal_version != session.proposal_version:
        raise LearningConflict(f"Proposal v{proposal_version} is not the current proposal (v{session.proposal_version}).")
    _validate(db, session)  # deterministic re-validation against the current state
    v = session.validation or {}
    if v.get("result") == learning_validation.PASSED:
        pass
    elif v.get("result") == learning_validation.NEEDS_REVIEW and v.get("compatibility_confirmation_required") and confirm_supersede:
        pass
    else:
        db.commit()  # keep the refreshed validation
        hint = (" Confirm with confirm_supersede=true to intentionally supersede old behavior."
                if v.get("compatibility_confirmation_required") else "")
        raise LearningConflict(f"Only a PASSED learning proposal can be approved; it is {v.get('result')}.{hint}")
    session.status = APPROVED
    session.approved_by = approved_by
    session.approved_at = datetime.now(tz=timezone.utc)
    _append(session, "APPROVED", approved_by, note=note, extra={
        "proposal_version": session.proposal_version, "target_version": session.target_version,
        "confirm_supersede": confirm_supersede,
        "match_rate": (v.get("new_structure") or {}).get("match_rate"),
    })
    db.commit()
    if activate_now:
        return activate(db, session.id, activated_by=approved_by)
    db.refresh(session)
    return session


def activate(db: Session, session_id: str, *, activated_by: str | None) -> LearningSession:
    session = _get(db, session_id, lock=True)
    if session.status == ACTIVE:
        db.commit()  # idempotent: release the lock, change nothing
        return session
    _require(session, (APPROVED,), "activate")
    active = _ensure_current_base(db, session)
    versions = onboarding_repo.adapter_versions(db, session.source_adapter_id)
    candidate = session.candidate
    new_norm = normalized_mapping(candidate)
    for v in versions:
        if normalized_mapping(v.mapping) == new_norm:
            raise LearningConflict(f"The learned mapping is identical to existing version v{v.version}; no new version is created.")
    version = max(v.version for v in versions) + 1
    now = datetime.now(tz=timezone.utc)
    active.status = ADAPTER_SUPERSEDED
    active.deactivated_at = now
    db.flush()
    validation = session.validation or {}
    row = OnboardedAdapter(
        adapter_id=session.source_adapter_id,
        version=version,
        status=ADAPTER_ACTIVE,
        mapping={**candidate, "version": str(version)},
        session_id=session.id,
        proposal_version=session.proposal_version,
        validation_summary={
            "origin": "phase6_learning",
            "learning_session_id": session.id,
            "learned_from_version": session.source_adapter_version,
            "trigger_event_id": session.trigger_event_id,
            "learning_modes": session.learning_modes,
            "result": validation.get("result"),
            "match_rate": (validation.get("new_structure") or {}).get("match_rate"),
            "mapping_coverage": (validation.get("new_structure") or {}).get("mapping_coverage"),
            "reasons": validation.get("reasons"),
        },
        approved_by=session.approved_by,
        approval_note=f"Phase 6 learning session {session.id}",
        approved_at=session.approved_at or now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LearningConflict("Another version was activated concurrently; retry.") from exc
    baseline_change = _adopt_learned_structure(db, session, version)
    session.status = ACTIVE
    session.activated_at = now
    session.target_adapter_row_id = row.id
    session.target_version = version
    _append(session, "ACTIVATED", activated_by, extra={
        "adapter_id": session.source_adapter_id, "version": version,
        "superseded_version": active.version, "baseline": baseline_change,
    })
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LearningConflict("Another version was activated concurrently; retry.") from exc
    db.refresh(session)
    logger.info("Learning session %s activated %s v%d.", session.id, session.source_adapter_id, version)
    return session


def rollback(db: Session, session_id: str, *, reason: str | None, requested_by: str | None) -> LearningSession:
    session = _get(db, session_id, lock=True)
    _require(session, (ACTIVE,), "roll back")
    row = db.get(OnboardedAdapter, session.target_adapter_row_id)
    if row is None or row.status != ADAPTER_ACTIVE:
        raise LearningConflict(f"Version v{session.target_version} is not the active version (it is {row.status if row else 'missing'}).")
    _append(session, "ROLLED_BACK", requested_by, note=reason, extra={"version": session.target_version})
    session.status = ROLLED_BACK
    db.flush()
    # Phase 3 rollback semantics: withdraw this version, reactivate the previous one (commits).
    onboarding_service.rollback(db, session.source_adapter_id, reason=reason, requested_by=requested_by)
    db.refresh(session)
    return session


# --------------------------------------------------------------------------
# Phase 5 feedback loop
# --------------------------------------------------------------------------


def _adopt_learned_structure(db: Session, session: LearningSession, version: int) -> dict[str, Any]:
    """The learned structure becomes the Phase 5 reference; the previous
    reference stays an approved variant, so older logs remain NORMAL and
    future drift is measured against the learned structure."""
    baseline = baseline_repo.get_baseline(db, session.source_key)
    if baseline is None:
        return {"changed": False, "reason": "no baseline"}
    new_fp = session.new_fingerprint
    previous = baseline.fingerprint
    if _same_structure(previous, new_fp):
        baseline.adapter_version = str(version)
        return {"changed": False, "reason": "learned structure already the reference"}
    variants = [v for v in (baseline.accepted_variants or []) if not _same_structure(v["fingerprint"], new_fp)]
    baseline.version += 1
    if not any(_same_structure(v["fingerprint"], previous) for v in variants):
        variants.insert(0, {"fingerprint": previous, "accepted_from_event_id": baseline.created_from_event_id,
                            "accepted_at": _now_iso(), "accepted_in_version": baseline.version,
                            "note": f"previous reference, retained by Phase 6 learning session {session.id}"})
    baseline.accepted_variants = variants[:MAX_BASELINE_VARIANTS]
    baseline.fingerprint = new_fp
    baseline.origin = ORIGIN_HUMAN_REVIEW
    baseline.adapter_version = str(version)
    baseline.created_from_event_id = session.trigger_event_id
    diff = structural_differences(new_fp, previous)
    baseline_repo.add_history(
        db, source_key=session.source_key, version=baseline.version, action=HISTORY_BASELINE_LEARNED,
        fingerprint=new_fp, event_id=session.trigger_event_id,
        changes={"added_fields": diff["added_fields"], "removed_fields": diff["removed_fields"],
                 "type_changes": diff["type_changes"], "order_changed": diff["order_changed"],
                 "change_types": drift_analysis.classify_changes(diff)},
        note=f"Phase 6 learning session {session.id} activated adapter {session.source_adapter_id} v{version}",
    )
    return {"changed": True, "baseline_version": baseline.version}


# --------------------------------------------------------------------------
# Helpers / responses
# --------------------------------------------------------------------------


def get_session(db: Session, session_id: str) -> LearningSession:
    return _get(db, session_id)


def _get(db: Session, session_id: str, *, lock: bool = False) -> LearningSession:
    session = learning_repo.get(db, session_id, lock=lock)
    if session is None:
        raise LearningNotFound(f"Learning session '{session_id}' not found")
    return session


def _ensure_current_base(db: Session, session: LearningSession) -> OnboardedAdapter:
    """A proposal is a delta against one specific version; it may only be
    approved/activated while that version is still the active one."""
    active = next((v for v in onboarding_repo.adapter_versions(db, session.source_adapter_id) if v.status == ADAPTER_ACTIVE), None)
    if active is None or active.id != session.source_adapter_row_id:
        current = f"v{active.version}" if active else "no version"
        raise LearningConflict(
            f"The proposal was built on v{session.source_adapter_version}, which is no longer the active version "
            f"({current} is); re-propose from the current version."
        )
    return active


def _require(session: LearningSession, allowed: tuple[str, ...], action: str) -> None:
    if session.status not in allowed:
        raise LearningConflict(f"Cannot {action} a learning session in state {session.status} (allowed: {', '.join(allowed)}).")


def _append(session: LearningSession, action: str, by: str | None, *, note: str | None = None, extra: dict | None = None) -> None:
    session.decisions = [*(session.decisions or []), _decision(action, by, note=note, extra=extra)]


def _decision(action: str, by: str | None, *, note: str | None = None, extra: dict | None = None) -> dict[str, Any]:
    return {"action": action, "by": by, "note": note, "at": _now_iso(), **(extra or {})}


def _ref(event) -> dict[str, Any]:
    return {"event_id": event.event_id, "raw_hash": event.raw_hash,
            "signature": (event.structural_fingerprint or {}).get("signature")}


def _raws(db: Session, refs: list[dict[str, Any]]) -> list[str]:
    raws = []
    for ref in refs:
        event = event_repo.get_event(db, ref["event_id"])
        if event is not None and event.raw_hash == ref["raw_hash"]:
            raws.append(event.raw_event)
    return raws


def _old_fingerprint(baseline, historical_events, new_fp) -> dict[str, Any] | None:
    if baseline is not None and not _same_structure(baseline.fingerprint, new_fp):
        return baseline.fingerprint
    for event in historical_events:
        if event.structural_fingerprint:
            return event.structural_fingerprint
    return None


def _same_structure(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b) and a.get("field_order") == b.get("field_order") and a.get("field_types") == b.get("field_types")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def session_dict(db: Session, session: LearningSession) -> dict[str, Any]:
    target_row = db.get(OnboardedAdapter, session.target_adapter_row_id) if session.target_adapter_row_id else None
    data = {
        "id": session.id, "status": session.status, "source_key": session.source_key,
        "source_adapter_id": session.source_adapter_id, "source_adapter_version": session.source_adapter_version,
        "target_version": session.target_version,
        "target_version_status": target_row.status if target_row else None,
        "trigger_event_id": session.trigger_event_id, "trigger_raw_hash": session.trigger_raw_hash,
        "drift": session.drift, "old_fingerprint": session.old_fingerprint, "new_fingerprint": session.new_fingerprint,
        "evidence": session.evidence, "learning_modes": session.learning_modes, "risk": session.risk,
        "risk_reasons": session.risk_reasons, "proposal": session.proposal, "proposal_version": session.proposal_version,
        "proposal_source": session.proposal_source, "assistant": session.assistant, "candidate": session.candidate,
        "mapping_diff": session.mapping_diff, "validation": session.validation, "decisions": session.decisions,
        "approved_by": session.approved_by, "approved_at": session.approved_at, "activated_at": session.activated_at,
        "rejection_reason": session.rejection_reason, "created_at": session.created_at, "updated_at": session.updated_at,
    }
    data["recommendation"] = recommendation(data)
    data["report"] = render_report(data)
    return data
