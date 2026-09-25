"""Structural drift detection and drift intelligence (Phase 5).

Known Source -> structural fingerprint -> compare against baseline ->
similarity + critical-field rules -> configurable threshold -> NORMAL or DRIFT
-> classification, severity, explanation, re-onboarding recommendation.

Runs in the service layer, between the (pure, DB-free) pipeline and
persistence, because it needs the stored per-source baselines. The
deterministic comparator (app.pipeline.drift.comparator) is the only
decision-maker; app.pipeline.drift.analysis grades and explains its
decision. Guarantees:

- Events from vendor-specific adapters (adapter_id == source_key) are
  compared against their source's baseline.
- Events routed to a *generic* adapter are never compared against a
  baseline of their own (a generic fallback aggregates unrelated sources).
  They are only checked for deterministic evidence that they come from a
  previously known vendor source whose adapter stopped matching; if so they
  are flagged POSSIBLE_FORMAT_DRIFT — never a definite drift claim.
- FAILED events are never touched.
- A drifted event is persisted in full (raw, hash, normalized event,
  extensions, fingerprint) with status UNDER_REVIEW; its pre-drift status
  is recorded as drift.original_status. Nothing is discarded.
- Drift evaluation can never make an event FAILED or lose it: any error
  inside it is contained (savepoint + exception handler) and recorded as
  drift.status = ERROR with the event's status left as the pipeline set it.
- Nothing here modifies parsers or adapters. Baselines change only through
  `accept`, an explicit human review action, and every change is recorded
  in the source's structural history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.loader import get_adapter_registry
from app.config import get_settings
from app.db.models.event import Event
from app.db.models.source_baseline import (
    HISTORY_BASELINE_CREATED,
    HISTORY_BASELINE_REPLACED,
    HISTORY_VARIANT_ADDED,
    ORIGIN_HUMAN_REVIEW,
    SourceBaseline,
)
from app.db.repository import baseline_repo, event_repo
from app.pipeline.drift import analysis
from app.pipeline.drift.comparator import REASON_FORMAT_CHANGED, compare, structural_differences
from app.pipeline.parsers.registry import get_parser
from app.schema.adapter import AdapterMapping
from app.schema.drift import BaselineHistoryEntry, BaselineResponse
from app.schema.ocsf import DriftStatus, EventStatus

logger = logging.getLogger(__name__)

# Upper bound on human-accepted variants per source. A source needing more
# than this is not "drifting", it needs a proper adapter rework — the
# reviewer should use replace_baseline (or fix the adapter) instead.
MAX_ACCEPTED_VARIANTS = 50

ACCEPT_ADD_VARIANT = "add_variant"
ACCEPT_REPLACE_BASELINE = "replace_baseline"
ACCEPT_ACKNOWLEDGE = "acknowledge"

REASON_ADAPTER_FALLBACK = "ADAPTER_FALLBACK"

_REVIEWABLE = (DriftStatus.DRIFT.value, DriftStatus.POSSIBLE_FORMAT_DRIFT.value)


class DriftConflictError(Exception):
    """The requested review action does not apply to the event's current state."""


# --------------------------------------------------------------------------
# Evaluation (ingestion / reprocess path)
# --------------------------------------------------------------------------


def evaluate(
    db: Session,
    fields: dict[str, Any],
    event_id: str,
    *,
    previous_review: dict[str, Any] | None = None,
) -> None:
    """Evaluate drift for an event about to be persisted, mutating its
    persistence field dict in place (status, processing_metadata.drift).
    Never raises; does not commit (the caller's event write does)."""
    settings = get_settings()
    if not settings.drift_enabled:
        return
    adapter = _evaluable_adapter(fields)
    if adapter is None:
        return

    threshold = settings.drift_similarity_threshold
    try:
        # Savepoint: if anything here fails at the DB level, only the drift
        # work is rolled back — the event insert that follows in the same
        # transaction is unaffected.
        with db.begin_nested():
            if adapter.match is not None:
                outcome = _evaluate_known_source(db, fields, event_id, adapter, threshold)
            else:
                outcome = _evaluate_adapter_fallback(db, fields, event_id, threshold)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Drift evaluation failed for adapter '%s'; event persisted without drift decision.", adapter.id
        )
        outcome = (
            {
                "status": DriftStatus.ERROR.value,
                "source_key": adapter.id,
                "threshold": threshold,
                "evaluated_at": _now_iso(),
                "error": f"Drift evaluation failed ({type(exc).__name__}). See server logs for details.",
            },
            False,
        )

    if outcome is None:  # generic-adapter event with no evidence of a known source
        return
    record, needs_review = outcome
    if previous_review:
        record["review"] = previous_review
    if needs_review:
        fields["status"] = EventStatus.UNDER_REVIEW.value
        logger.info(
            "%s for source '%s' (severity %s, similarity %s); event %s marked UNDER_REVIEW.",
            record["status"],
            record["source_key"],
            record.get("severity"),
            record.get("similarity"),
            event_id,
        )
    fields["processing_metadata"] = {**(fields.get("processing_metadata") or {}), "drift": record}


def _evaluable_adapter(fields: dict[str, Any]) -> AdapterMapping | None:
    if fields.get("status") == EventStatus.FAILED.value:
        return None
    adapter_id = fields.get("adapter_id")
    if not adapter_id or not fields.get("structural_fingerprint"):
        return None
    return get_adapter_registry().get(adapter_id)


def _evaluate_known_source(
    db: Session,
    fields: dict[str, Any],
    event_id: str,
    adapter: AdapterMapping,
    threshold: float,
) -> tuple[dict[str, Any], bool]:
    source_key = adapter.id
    fingerprint = fields["structural_fingerprint"]
    baseline = baseline_repo.get_baseline(db, source_key)
    if baseline is None:
        baseline, created = baseline_repo.insert_if_absent(
            db,
            source_key=source_key,
            adapter_id=adapter.id,
            adapter_version=fields.get("adapter_version"),
            format_detected=fields["format_detected"],
            fingerprint=fingerprint,
            created_from_event_id=event_id,
        )
        if created:
            baseline_repo.add_history(
                db,
                source_key=source_key,
                version=baseline.version,
                action=HISTORY_BASELINE_CREATED,
                fingerprint=fingerprint,
                event_id=event_id,
                note="Provisional baseline auto-bootstrapped from the first observed structure.",
            )
            return (
                {
                    "status": DriftStatus.BASELINE_CREATED.value,
                    "source_key": source_key,
                    "baseline_version": baseline.version,
                    "baseline_origin": baseline.origin,
                    "matched": "reference",
                    "similarity": 1.0,
                    "threshold": threshold,
                    "evaluated_at": _now_iso(),
                },
                False,
            )

    critical_fields = analysis.resolve_critical_fields(adapter, get_settings().drift_critical_fields_list)
    comparison = compare(
        fingerprint,
        baseline.fingerprint,
        [variant["fingerprint"] for variant in baseline.accepted_variants or []],
        threshold=threshold,
        current_format=fields["format_detected"],
        baseline_format=baseline.format_detected,
        critical_fields=critical_fields,
    )
    record: dict[str, Any] = {
        "status": (DriftStatus.DRIFT if comparison.is_drift else DriftStatus.NORMAL).value,
        "source_key": source_key,
        "baseline_version": baseline.version,
        "baseline_origin": baseline.origin,
        "matched": comparison.matched,
        "similarity": comparison.similarity,
        "threshold": threshold,
        "evaluated_at": _now_iso(),
    }
    # A NORMAL event that is not an exact structural match still carries
    # the breakdown and change types, so minor tolerated changes stay visible.
    if comparison.is_drift or comparison.similarity < 1.0:
        record["components"] = comparison.components
        record["differences"] = comparison.differences
        record["change_types"] = analysis.classify_changes(comparison.differences)
    if comparison.is_drift:
        severity, score, factors = analysis.compute_severity(
            comparison.differences,
            comparison.components,
            comparison.critical_changes,
            format_changed=REASON_FORMAT_CHANGED in comparison.decision_reasons,
        )
        record.update(
            {
                "decision_reasons": comparison.decision_reasons,
                "severity": severity,
                "severity_score": score,
                "severity_factors": factors,
                "critical_field_changes": comparison.critical_changes,
                "recommended_actions": analysis.recommend_actions(
                    record["change_types"], comparison.critical_changes
                ),
                "original_status": fields["status"],
                "reonboarding_required": True,
                "recommended_action": (
                    f"The structure of known source '{source_key}' changed. Review the differences and, if "
                    f"the change is legitimate, update adapter '{source_key}' manually if needed and accept "
                    f"the new structure via POST /api/v1/events/{event_id}/drift/accept "
                    f"(mode 'add_variant' or 'replace_baseline')."
                ),
            }
        )
    return record, comparison.is_drift


def _evaluate_adapter_fallback(
    db: Session, fields: dict[str, Any], event_id: str, threshold: float
) -> tuple[dict[str, Any], bool] | None:
    """A generic-adapter event: look for deterministic evidence that it comes
    from a previously known vendor source (one with a baseline) whose
    adapter no longer matched. No evidence -> no drift record at all."""
    registry = get_adapter_registry()
    parsed_fields: dict[str, Any] | None = None
    best: tuple[SourceBaseline, AdapterMapping, list[dict[str, str]]] | None = None
    for baseline in baseline_repo.list_baselines(db):
        vendor = registry.get(baseline.adapter_id)
        if vendor is None or vendor.match is None:
            continue
        if parsed_fields is None:
            parsed_fields = _reparse(fields)
        evidence = analysis.fallback_evidence(
            vendor,
            event_format=fields["format_detected"],
            parsed_fields=parsed_fields,
            raw_event=fields["raw_event"],
        )
        # Most evidence wins; ties keep the first (baselines are ordered by source_key).
        if evidence and (best is None or len(evidence) > len(best[2])):
            best = (baseline, vendor, evidence)
    if best is None:
        return None

    baseline, vendor, evidence = best
    comparison = compare(
        fields["structural_fingerprint"],
        baseline.fingerprint,
        [variant["fingerprint"] for variant in baseline.accepted_variants or []],
        threshold=threshold,
        current_format=fields["format_detected"],
        baseline_format=baseline.format_detected,
    )
    format_changed = fields["format_detected"] != baseline.format_detected
    severity, score, factors = analysis.compute_severity(
        None, None, None, adapter_fallback=True, format_changed=format_changed
    )
    reasons = [REASON_ADAPTER_FALLBACK] + ([REASON_FORMAT_CHANGED] if format_changed else [])
    change_types = analysis.classify_changes(comparison.differences, format_drift=True)
    record = {
        "status": DriftStatus.POSSIBLE_FORMAT_DRIFT.value,
        "source_key": baseline.source_key,
        "current_adapter": fields["adapter_id"],
        "baseline_version": baseline.version,
        "baseline_origin": baseline.origin,
        "similarity": comparison.similarity,
        "threshold": threshold,
        "components": comparison.components,
        "differences": comparison.differences,
        "change_types": change_types,
        "decision_reasons": reasons,
        "severity": severity,
        "severity_score": score,
        "severity_factors": factors,
        "evidence": evidence,
        "recommended_actions": analysis.recommend_actions(change_types, None),
        "original_status": fields["status"],
        "reonboarding_required": True,
        "recommended_action": (
            f"This event was routed to generic adapter '{fields['adapter_id']}', but carries evidence of "
            f"previously known source '{baseline.source_key}'. Its vendor adapter may no longer match a "
            f"changed log format. Verify the source; if confirmed, update adapter '{vendor.id}' manually. "
            f"Then acknowledge via POST /api/v1/events/{event_id}/drift/accept (mode 'acknowledge')."
        ),
        "evaluated_at": _now_iso(),
    }
    return record, True


def _reparse(fields: dict[str, Any]) -> dict[str, Any]:
    """Parser-level fields of the event (the pipeline only keeps them split
    across normalized_event/extensions). Deterministic and side-effect free;
    any parse problem simply yields no field-based evidence."""
    parser = get_parser(fields["format_detected"])
    if parser is None:
        return {}
    try:
        return parser.parse(fields["raw_event"]).fields
    except Exception:  # noqa: BLE001
        return {}


# --------------------------------------------------------------------------
# Human review
# --------------------------------------------------------------------------


def accept(db: Session, event: Event, mode: str, note: str | None = None) -> tuple[Event, SourceBaseline]:
    """Human review of a drifted event. Updates the source baseline (and its
    history) as requested and restores the event's pre-drift status, in one
    commit. `acknowledge` restores status without touching the baseline."""
    metadata = dict(event.processing_metadata or {})
    drift = metadata.get("drift")
    if (
        event.status != EventStatus.UNDER_REVIEW.value
        or not isinstance(drift, dict)
        or drift.get("status") not in _REVIEWABLE
    ):
        raise DriftConflictError(f"Event '{event.event_id}' is not UNDER_REVIEW due to structural drift.")
    if drift["status"] == DriftStatus.POSSIBLE_FORMAT_DRIFT.value and mode != ACCEPT_ACKNOWLEDGE:
        raise DriftConflictError(
            f"Event '{event.event_id}' is a POSSIBLE_FORMAT_DRIFT routed to a generic adapter; its structure "
            f"cannot be added to source '{drift['source_key']}'. Fix the vendor adapter manually if needed "
            "and use mode 'acknowledge'."
        )
    if mode != ACCEPT_ACKNOWLEDGE and not event.structural_fingerprint:
        raise DriftConflictError(f"Event '{event.event_id}' has no structural fingerprint to accept.")

    source_key = drift["source_key"]
    baseline = baseline_repo.get_baseline(db, source_key)
    if baseline is None:
        raise DriftConflictError(f"No baseline exists for source '{source_key}'.")

    now = _now_iso()
    fingerprint = event.structural_fingerprint
    if mode == ACCEPT_ACKNOWLEDGE:
        resolution = "acknowledged"
    elif mode == ACCEPT_ADD_VARIANT:
        _add_variant(db, baseline, event, fingerprint, note, now)
        resolution = "accepted_variant"
    elif mode == ACCEPT_REPLACE_BASELINE:
        changes = _history_changes(fingerprint, baseline.fingerprint)
        baseline.fingerprint = fingerprint
        baseline.accepted_variants = []
        baseline.format_detected = event.format_detected
        baseline.adapter_version = event.adapter_version
        baseline.created_from_event_id = event.event_id
        baseline.origin = ORIGIN_HUMAN_REVIEW
        baseline.version += 1
        baseline_repo.add_history(
            db,
            source_key=source_key,
            version=baseline.version,
            action=HISTORY_BASELINE_REPLACED,
            fingerprint=fingerprint,
            event_id=event.event_id,
            changes=changes,
            note=note,
        )
        resolution = "replaced_baseline"
    else:
        raise DriftConflictError(f"Unknown accept mode '{mode}'.")

    metadata["drift"] = {
        **drift,
        "reonboarding_required": False,
        "review": {
            "resolution": resolution,
            "reviewed_at": now,
            "note": note,
            "baseline_version": baseline.version,
        },
    }
    restored_status = drift.get("original_status") or EventStatus.SUCCESS.value
    db.add(baseline)
    updated = event_repo.update_event_fields(
        db, event, {"status": restored_status, "processing_metadata": metadata}
    )  # commits the baseline + history change in the same transaction
    db.refresh(baseline)
    logger.info("Drift on event %s reviewed (%s); baseline '%s' now v%d.", event.event_id, mode, source_key, baseline.version)
    return updated, baseline


def _add_variant(
    db: Session,
    baseline: SourceBaseline,
    event: Event,
    fingerprint: dict[str, Any],
    note: str | None,
    now: str,
) -> None:
    """Adds the structure as an approved variant unless it is already known
    (identical to the reference or to any approved variant) — duplicates
    are never added and never bump the version."""
    variants = list(baseline.accepted_variants or [])
    known = [baseline.fingerprint] + [v["fingerprint"] for v in variants]
    if any(_same_structure(existing, fingerprint) for existing in known):
        return
    if len(variants) >= MAX_ACCEPTED_VARIANTS:
        raise DriftConflictError(
            f"Source '{baseline.source_key}' already has {MAX_ACCEPTED_VARIANTS} accepted variants; "
            "use mode 'replace_baseline' or rework the adapter."
        )
    baseline.version += 1
    variants.append(
        {
            "fingerprint": fingerprint,
            "accepted_from_event_id": event.event_id,
            "accepted_at": now,
            "accepted_in_version": baseline.version,
            "note": note,
        }
    )
    baseline.accepted_variants = variants  # reassign so the JSONB change is detected
    baseline_repo.add_history(
        db,
        source_key=baseline.source_key,
        version=baseline.version,
        action=HISTORY_VARIANT_ADDED,
        fingerprint=fingerprint,
        event_id=event.event_id,
        changes=_history_changes(fingerprint, baseline.fingerprint),
        note=note,
    )


def _history_changes(new: dict[str, Any], old_reference: dict[str, Any]) -> dict[str, Any]:
    diff = structural_differences(new, old_reference)
    return {
        "added_fields": diff["added_fields"],
        "removed_fields": diff["removed_fields"],
        "type_changes": diff["type_changes"],
        "order_changed": diff["order_changed"],
        "change_types": analysis.classify_changes(diff),
    }


def previous_review(event: Event) -> dict[str, Any] | None:
    """The human review recorded on an event's drift record, if any — carried
    forward across reprocessing so the audit trail is never lost."""
    drift = (event.processing_metadata or {}).get("drift")
    if isinstance(drift, dict) and isinstance(drift.get("review"), dict):
        return drift["review"]
    return None


# --------------------------------------------------------------------------
# Baseline queries
# --------------------------------------------------------------------------


def list_baselines(db: Session) -> list[BaselineResponse]:
    baselines = baseline_repo.list_baselines(db)
    counts = baseline_repo.under_review_counts(db)
    return [_to_response(b, counts.get(b.source_key, 0)) for b in baselines]


def get_baseline(db: Session, source_key: str) -> BaselineResponse | None:
    baseline = baseline_repo.get_baseline(db, source_key)
    if baseline is None:
        return None
    return to_response(db, baseline)


def to_response(db: Session, baseline: SourceBaseline) -> BaselineResponse:
    """Full baseline view, including its structural history."""
    counts = baseline_repo.under_review_counts(db, [baseline.source_key])
    history = [
        BaselineHistoryEntry.model_validate(h, from_attributes=True)
        for h in baseline_repo.list_history(db, baseline.source_key)
    ]
    return _to_response(baseline, counts.get(baseline.source_key, 0), history)


def _to_response(
    baseline: SourceBaseline, under_review_count: int, history: list[BaselineHistoryEntry] | None = None
) -> BaselineResponse:
    response = BaselineResponse.model_validate(baseline, from_attributes=True)
    response.under_review_count = under_review_count
    response.history = history
    return response


def _same_structure(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("field_order") == b.get("field_order") and a.get("field_types") == b.get("field_types")


def _now_iso() -> str:
    # Stored inside JSONB, so kept as an ISO-8601 string.
    return datetime.now(tz=timezone.utc).isoformat()
