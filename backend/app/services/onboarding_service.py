"""Adaptive unknown-vendor onboarding workflow.

UNKNOWN SOURCE -> samples -> deterministic multi-sample analysis ->
suggestion (Claude or offline analyzer; untrusted) -> strict schema +
evidence review -> sandbox validation on every sample (real runtime
pipeline) -> PASSED / NEEDS_REVIEW / REJECTED -> HUMAN APPROVAL ->
immutable versioned adapter (ACTIVE) -> future logs parsed by the normal
deterministic pipeline, with no LLM involved.

Governance invariants enforced here:
- Nothing becomes active without an explicit human approval of a specific,
  PASSED proposal version; approval re-runs the sandbox deterministically.
- Samples are never modified or dropped, whatever fails.
- An approved adapter never silently replaces another: a new version is a
  new immutable row; the previous one is kept (SUPERSEDED) for rollback.
- Adapter ids of shipped YAML adapters can never be claimed.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.loader import AdapterRegistry, get_adapter_registry
from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.onboarding import (
    ADAPTER_ACTIVE,
    ADAPTER_ROLLED_BACK,
    ADAPTER_SUPERSEDED,
    SESSION_APPROVED,
    SESSION_COLLECTED,
    SESSION_REJECTED,
    SESSION_SUGGESTION_FAILED,
    SESSION_VALIDATED,
    OnboardedAdapter,
    OnboardingSession,
)
from app.db.repository import event_repo, onboarding_repo
from app.onboarding import sandbox
from app.onboarding.analysis import MAX_SAMPLE_CHARS, MAX_SAMPLES, analyze_samples
from app.onboarding.explain import render_session_explanation
from app.onboarding.proposal import (
    ADAPTER_ID_RE,
    AdapterProposal,
    review_proposal,
    suggested_adapter_id,
    to_adapter,
)
from app.onboarding.providers import (
    INSUFFICIENT_EVIDENCE,
    UNAVAILABLE,
    SuggestionError,
    SuggestionRequest,
    get_provider,
    parse_proposal,
)
from app.pipeline.hashing import sha256_hex
from app.schema.adapter import AdapterMapping
from app.schema.onboarding import Activation, AdapterVersionResponse, SessionResponse, SessionSummary

logger = logging.getLogger(__name__)


class OnboardingError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OnboardingNotFound(OnboardingError):
    status_code = 404


class OnboardingConflict(OnboardingError):
    status_code = 409


class OnboardingUnprocessable(OnboardingError):
    status_code = 422


class OnboardingUpstreamError(OnboardingError):
    status_code = 502


# --------------------------------------------------------------------------
# Sessions and samples
# --------------------------------------------------------------------------


def create_session(db: Session, *, samples: list[str], event_ids: list[str], name: str | None) -> OnboardingSession:
    collected: list[dict[str, Any]] = [{"raw": s, "source_event_id": None} for s in samples]
    for event_id in event_ids:
        event = event_repo.get_event(db, event_id)
        if event is None:
            raise OnboardingNotFound(f"Event '{event_id}' not found")
        collected.append({"raw": event.raw_event, "source_event_id": event_id})
    if not collected:
        raise OnboardingUnprocessable("Provide at least one sample (samples or event_ids).")
    if len(collected) > MAX_SAMPLES:
        raise OnboardingUnprocessable(f"At most {MAX_SAMPLES} samples per onboarding session.")
    for i, item in enumerate(collected):
        if len(item["raw"]) > MAX_SAMPLE_CHARS:
            raise OnboardingUnprocessable(f"Sample {i} exceeds {MAX_SAMPLE_CHARS} characters.")

    stored = [
        {"index": i, "raw": item["raw"], "raw_hash": sha256_hex(item["raw"]), "source_event_id": item["source_event_id"]}
        for i, item in enumerate(collected)
    ]
    session = OnboardingSession(
        id=generate_event_id(),
        name=name,
        status=SESSION_COLLECTED,
        samples=stored,
        sample_count=len(stored),
        analysis=analyze_samples([s["raw"] for s in stored]),
        proposal_version=0,
        decisions=[],
    )
    onboarding_repo.add_session(db, session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: str) -> OnboardingSession:
    session = onboarding_repo.get_session(db, session_id)
    if session is None:
        raise OnboardingNotFound(f"Onboarding session '{session_id}' not found")
    return session


# --------------------------------------------------------------------------
# Suggestion + validation
# --------------------------------------------------------------------------


def suggest(db: Session, session: OnboardingSession, provider_choice: str) -> OnboardingSession:
    _ensure_open(session)
    samples = [s["raw"] for s in session.samples]
    provider_name = provider_choice
    try:
        provider = get_provider(provider_choice)
        provider_name = provider.name
        raw_output = provider.suggest(SuggestionRequest(samples=samples, analysis=session.analysis))
        proposal = parse_proposal(raw_output)
    except SuggestionError as exc:
        _record_suggestion_failure(db, session, provider_name, exc.kind, exc.message, exc.raw_output)
        if exc.kind == INSUFFICIENT_EVIDENCE:
            raise OnboardingUnprocessable(exc.message) from exc
        raise OnboardingUpstreamError(f"Suggestion failed ({exc.kind}): {exc.message} The samples are preserved.") from exc
    except Exception as exc:  # noqa: BLE001 — a provider bug must never lose the session
        logger.exception("Onboarding provider '%s' failed unexpectedly.", provider_name)
        _record_suggestion_failure(db, session, provider_name, UNAVAILABLE, f"Provider error ({type(exc).__name__}).", None)
        raise OnboardingUpstreamError("Suggestion failed (UNAVAILABLE). The samples are preserved.") from exc
    return _evaluate(db, session, proposal, source=provider_name)


def submit_proposal(db: Session, session: OnboardingSession, proposal_data: dict[str, Any]) -> OnboardingSession:
    """A human-written/edited proposal goes through exactly the same gates."""
    _ensure_open(session)
    try:
        proposal = parse_proposal(proposal_data)
    except SuggestionError as exc:
        raise OnboardingUnprocessable(exc.message) from exc
    return _evaluate(db, session, proposal, source="human")


def _record_suggestion_failure(db, session, provider_name, kind, message, raw_output) -> None:
    session.suggestion_error = {
        "kind": kind,
        "message": message,
        "provider": provider_name,
        "raw_output_excerpt": (raw_output or "")[:2000] or None,
        "at": _now_iso(),
    }
    if session.proposal is None:
        session.status = SESSION_SUGGESTION_FAILED
    db.commit()
    db.refresh(session)


def _evaluate(db: Session, session: OnboardingSession, proposal: AdapterProposal, *, source: str) -> OnboardingSession:
    evaluation = _run_validation(db, session, proposal, adapter_id=suggested_adapter_id(proposal))
    session.proposal = proposal.model_dump(mode="json")
    session.proposal_version += 1
    session.proposal_source = source
    session.suggestion_error = None
    session.validation = {**evaluation, "proposal_version": session.proposal_version, "validated_at": _now_iso()}
    session.status = SESSION_VALIDATED
    db.commit()
    db.refresh(session)
    return session


def _run_validation(db: Session, session: OnboardingSession, proposal: AdapterProposal, *, adapter_id: str) -> dict[str, Any]:
    """Evidence review + sandbox on every sample. Deterministic for a given
    proposal, sample set and set of active adapters."""
    settings = get_settings()
    review = review_proposal(proposal, session.analysis)
    version = _next_version(db, adapter_id)
    metrics = None
    preview = None
    if adapter_id in {a.id for a in get_adapter_registry().all()}:
        review["issues"].append(
            f"Adapter id '{adapter_id}' belongs to a shipped adapter; a known source cannot be re-onboarded "
            "(its structural changes are handled by drift review)."
        )
    if not review["issues"]:
        try:
            candidate = to_adapter(
                proposal, review["accepted"], adapter_id=adapter_id, version=version,
                description=f"Onboarded via session {session.id}",
            )
        except (ValidationError, ValueError) as exc:
            review["issues"].append(f"The proposal does not form a valid adapter: {str(exc)[:300]}")
        else:
            metrics = sandbox.run_sandbox([s["raw"] for s in session.samples], candidate, runtime_registry(db))
            preview = candidate.model_dump(mode="json")
    result, reasons = sandbox.decide(
        review,
        metrics,
        min_match_rate=settings.onboarding_min_match_rate,
        reject_below_match_rate=settings.onboarding_reject_below_match_rate,
        min_mapping_coverage=settings.onboarding_min_mapping_coverage,
    )
    return {
        "result": result,
        "reasons": reasons,
        "issues": review["issues"],
        "accepted_mappings": review["accepted"],
        "rejected_mappings": review["rejected"],
        "metrics": metrics,
        "adapter_preview": preview,
        "thresholds": {
            "min_match_rate": settings.onboarding_min_match_rate,
            "reject_below_match_rate": settings.onboarding_reject_below_match_rate,
            "min_mapping_coverage": settings.onboarding_min_mapping_coverage,
        },
    }


# --------------------------------------------------------------------------
# Human decisions
# --------------------------------------------------------------------------


def approve(
    db: Session,
    session: OnboardingSession,
    *,
    proposal_version: int,
    adapter_id: str | None,
    approved_by: str | None,
    note: str | None,
) -> tuple[OnboardingSession, OnboardedAdapter]:
    if session.status == SESSION_APPROVED:
        raise OnboardingConflict(f"Session '{session.id}' is already approved (adapter '{session.adapter_id}' v{session.adapter_version}).")
    if session.status != SESSION_VALIDATED or not session.proposal or not session.validation:
        raise OnboardingConflict(f"Session '{session.id}' has no validated proposal to approve (status {session.status}).")
    if proposal_version != session.proposal_version:
        raise OnboardingConflict(
            f"Proposal v{proposal_version} is not the current proposal (v{session.proposal_version}); review the current one."
        )
    if session.validation.get("result") != sandbox.PASSED:
        raise OnboardingConflict(
            f"Only a PASSED proposal can be approved; this one is {session.validation.get('result')}. "
            "Revise the proposal and re-validate."
        )

    proposal = AdapterProposal.model_validate(session.proposal)
    adapter_id = adapter_id or suggested_adapter_id(proposal)
    if not ADAPTER_ID_RE.match(adapter_id):
        raise OnboardingUnprocessable("adapter_id must be 3-64 characters: lowercase letters, digits, underscores, starting with a letter.")

    # Deterministic re-validation against the adapters active *now*.
    evaluation = _run_validation(db, session, proposal, adapter_id=adapter_id)
    if evaluation["result"] != sandbox.PASSED:
        raise OnboardingConflict("Re-validation at approval did not pass: " + " ".join(evaluation["reasons"]))
    mapping = evaluation["adapter_preview"]

    versions = onboarding_repo.adapter_versions(db, adapter_id)
    active = next((v for v in versions if v.status == ADAPTER_ACTIVE), None)
    if active is not None and _same_mapping(active.mapping, mapping):
        raise OnboardingConflict(f"The proposal is identical to the active version v{active.version} of '{adapter_id}'.")
    now = datetime.now(tz=timezone.utc)
    if active is not None:
        active.status = ADAPTER_SUPERSEDED
        active.deactivated_at = now
        db.flush()  # free the single-ACTIVE slot before inserting the new version

    metrics = evaluation["metrics"]
    row = OnboardedAdapter(
        adapter_id=adapter_id,
        version=int(mapping["version"]),
        status=ADAPTER_ACTIVE,
        mapping=mapping,
        session_id=session.id,
        proposal_version=session.proposal_version,
        validation_summary={
            "result": evaluation["result"],
            "match_rate": metrics["match_rate"],
            "mapping_coverage": metrics["mapping_coverage"],
            "matched_samples": metrics["matched_samples"],
            "total_samples": metrics["total_samples"],
            "reasons": evaluation["reasons"],
        },
        approved_by=approved_by,
        approval_note=note,
        approved_at=now,
    )
    db.add(row)
    session.status = SESSION_APPROVED
    session.adapter_id = adapter_id
    session.adapter_version = row.version
    session.validation = {**evaluation, "proposal_version": session.proposal_version, "validated_at": _now_iso()}
    session.decisions = [
        *session.decisions,
        {
            "action": "APPROVED",
            "by": approved_by,
            "note": note,
            "at": now.isoformat(),
            "proposal_version": session.proposal_version,
            "adapter_id": adapter_id,
            "adapter_version": row.version,
            "superseded_version": active.version if active else None,
            "match_rate": metrics["match_rate"],
        },
    ]
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise OnboardingConflict(f"Adapter '{adapter_id}' was changed concurrently; retry the approval.") from exc
    db.refresh(session)
    db.refresh(row)
    logger.info("Onboarding session %s approved: adapter '%s' v%d is ACTIVE.", session.id, adapter_id, row.version)
    return session, row


def reject(db: Session, session: OnboardingSession, *, reason: str, rejected_by: str | None) -> OnboardingSession:
    _ensure_open(session)
    session.status = SESSION_REJECTED
    session.decisions = [
        *session.decisions,
        {"action": "REJECTED", "by": rejected_by, "reason": reason, "at": _now_iso(),
         "proposal_version": session.proposal_version},
    ]
    db.commit()
    db.refresh(session)
    return session


def rollback(db: Session, adapter_id: str, *, reason: str | None, requested_by: str | None) -> list[OnboardedAdapter]:
    """Human rollback: the ACTIVE version is withdrawn and the most recent
    earlier (SUPERSEDED) version, if any, becomes ACTIVE again."""
    versions = onboarding_repo.adapter_versions(db, adapter_id)
    if not versions:
        raise OnboardingNotFound(f"Onboarded adapter '{adapter_id}' not found")
    active = next((v for v in versions if v.status == ADAPTER_ACTIVE), None)
    if active is None:
        raise OnboardingConflict(f"Adapter '{adapter_id}' has no active version to roll back.")
    now = datetime.now(tz=timezone.utc)
    active.status = ADAPTER_ROLLED_BACK
    active.deactivated_at = now
    db.flush()
    previous = max((v for v in versions if v.status == ADAPTER_SUPERSEDED and v.version < active.version),
                   key=lambda v: v.version, default=None)
    if previous is not None:
        previous.status = ADAPTER_ACTIVE
        previous.deactivated_at = None
    source = onboarding_repo.get_session(db, active.session_id)
    if source is not None:
        source.decisions = [
            *source.decisions,
            {"action": "ROLLED_BACK", "by": requested_by, "reason": reason, "at": now.isoformat(),
             "adapter_id": adapter_id, "adapter_version": active.version,
             "reactivated_version": previous.version if previous else None},
        ]
    db.commit()
    return onboarding_repo.adapter_versions(db, adapter_id)


def _ensure_open(session: OnboardingSession) -> None:
    if session.status == SESSION_APPROVED:
        raise OnboardingConflict(
            f"Session '{session.id}' is approved and immutable; start a new session to onboard a new version."
        )


def _next_version(db: Session, adapter_id: str) -> int:
    versions = onboarding_repo.adapter_versions(db, adapter_id)
    return max((v.version for v in versions), default=0) + 1


def _same_mapping(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ignored = {"version", "description"}
    return {k: v for k, v in a.items() if k not in ignored} == {k: v for k, v in b.items() if k not in ignored}


# --------------------------------------------------------------------------
# Runtime registry: shipped YAML adapters + ACTIVE onboarded adapters
# --------------------------------------------------------------------------

_registry_lock = threading.Lock()
_registry_cache: dict[str, Any] = {"key": None, "registry": None}


def runtime_registry(db: Session) -> AdapterRegistry:
    """The adapter registry the ingestion pipeline uses. Built only from
    shipped YAML and human-approved ACTIVE onboarded adapters — no LLM, no
    network. Any problem reading onboarded adapters degrades to the shipped
    registry (Phase 0-4 behavior) instead of failing ingestion."""
    shipped = get_adapter_registry()
    try:
        with db.begin_nested():
            keys = onboarding_repo.active_adapter_keys(db)
            if not keys:
                return shipped
            with _registry_lock:
                if _registry_cache["key"] == keys:
                    return _registry_cache["registry"]
            adapters: list[AdapterMapping] = []
            for row in onboarding_repo.active_adapters(db):
                try:
                    adapters.append(AdapterMapping.model_validate({**row.mapping, "source": "onboarded"}))
                except ValidationError:
                    logger.error("Onboarded adapter '%s' v%d failed validation and is skipped.", row.adapter_id, row.version)
        registry = AdapterRegistry(shipped.all() + adapters)
        with _registry_lock:
            _registry_cache.update(key=keys, registry=registry)
        return registry
    except Exception:  # noqa: BLE001
        logger.exception("Could not load onboarded adapters; using shipped adapters only.")
        return shipped


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


def session_summary(session: OnboardingSession) -> SessionSummary:
    validation = session.validation or {}
    return SessionSummary(
        id=session.id,
        name=session.name,
        status=session.status,
        sample_count=session.sample_count,
        proposal_version=session.proposal_version,
        validation_result=validation.get("result"),
        match_rate=(validation.get("metrics") or {}).get("match_rate"),
        adapter_id=session.adapter_id,
        adapter_version=session.adapter_version,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def session_response(db: Session, session: OnboardingSession) -> SessionResponse:
    data = {
        "id": session.id,
        "status": session.status,
        "sample_count": session.sample_count,
        "analysis": session.analysis,
        "proposal": session.proposal,
        "proposal_version": session.proposal_version,
        "proposal_source": session.proposal_source,
        "suggestion_error": session.suggestion_error,
        "validation": session.validation,
        "adapter_id": session.adapter_id,
        "adapter_version": session.adapter_version,
    }
    return SessionResponse(
        **session_summary(session).model_dump(),
        samples=session.samples,
        analysis=session.analysis,
        proposal=session.proposal,
        proposal_source=session.proposal_source,
        suggestion_error=session.suggestion_error,
        validation=session.validation,
        decisions=session.decisions,
        activation=_activation(db, session),
        explanation=render_session_explanation(data, get_settings().onboarding_recommended_samples),
    )


def _activation(db: Session, session: OnboardingSession) -> Activation:
    result = (session.validation or {}).get("result")
    if session.status == SESSION_APPROVED:
        row = next((v for v in onboarding_repo.adapter_versions(db, session.adapter_id or "")
                    if v.version == session.adapter_version), None)
        state = "ACTIVE" if row is not None and row.status == ADAPTER_ACTIVE else (row.status if row else "UNKNOWN")
        return Activation(active=state == "ACTIVE", state=state, eligible_for_approval=False,
                          adapter_id=session.adapter_id, adapter_version=session.adapter_version)
    if session.status == SESSION_REJECTED:
        return Activation(active=False, state="REJECTED", eligible_for_approval=False)
    if session.status != SESSION_VALIDATED:
        return Activation(active=False, state="NOT_ACTIVE_NO_PROPOSAL", eligible_for_approval=False)
    if result == sandbox.PASSED:
        return Activation(active=False, state="NOT_ACTIVE_AWAITING_APPROVAL", eligible_for_approval=True)
    return Activation(active=False, state="NOT_ACTIVE_NOT_ELIGIBLE", eligible_for_approval=False)


def adapter_version_response(row: OnboardedAdapter) -> AdapterVersionResponse:
    return AdapterVersionResponse.model_validate(row, from_attributes=True)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
