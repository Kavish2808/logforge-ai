"""Deterministic structural drift comparison.

Compares an event's structural fingerprint (app.pipeline.fingerprint)
against a source's baseline structure(s) and produces a similarity score
in [0, 1] plus a human-readable breakdown of what changed. Pure function:
no DB, no network, standard library only.

The score is a weighted sum of four signals. The weights are MVP
heuristics — they are named constants so they are easy to find and tune,
but deliberately not runtime configuration. Only the decision threshold
is configurable (DRIFT_SIMILARITY_THRESHOLD).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# --- MVP heuristic weights (must sum to 1.0) ---
WEIGHT_FIELD_SET = 0.55  # field presence/absence (Jaccard of field sets)
WEIGHT_FIELD_ORDER = 0.20  # field ordering (sequence similarity of field_order)
WEIGHT_FIELD_COUNT = 0.10  # field count ratio (min/max)
WEIGHT_FIELD_TYPES = 0.15  # value-type consistency of shared fields (schema)

# Scores are rounded before the threshold decision, so the reported
# similarity and the NORMAL/DRIFT decision can never disagree because of
# floating-point noise (e.g. a reported 0.85 that was really 0.8499999).
SCORE_PRECISION = 4


REASON_SIMILARITY_BELOW_THRESHOLD = "SIMILARITY_BELOW_THRESHOLD"
REASON_CRITICAL_FIELD_CHANGED = "CRITICAL_FIELD_CHANGED"
REASON_FORMAT_CHANGED = "FORMAT_CHANGED"


@dataclass
class DriftComparison:
    similarity: float
    is_drift: bool
    matched: str  # "reference" or "variant:<index>"
    components: dict[str, float]
    differences: dict[str, Any] = field(default_factory=dict)
    # Critical fields (present in the matched structure) that were removed
    # or changed type: [{"field", "target", "change", "baseline_type", "current_type"}]
    critical_changes: list[dict[str, Any]] = field(default_factory=list)
    # Why the event drifted (empty when NORMAL).
    decision_reasons: list[str] = field(default_factory=list)


def compare(
    current: dict[str, Any],
    reference: dict[str, Any],
    variants: list[dict[str, Any]] | None = None,
    *,
    threshold: float,
    current_format: str | None = None,
    baseline_format: str | None = None,
    critical_fields: dict[str, str | None] | None = None,
) -> DriftComparison:
    """Score `current` against the baseline reference and each accepted variant.

    A candidate structure *accepts* the event when its similarity is at or
    above `threshold` and no critical field (source field name -> OCSF
    target label) was removed or changed type relative to it. If any
    candidate accepts, the event is NORMAL and the best-scoring accepting
    candidate is reported as `matched`. Otherwise the event drifts and the
    best-scoring candidate overall is reported. A format mismatch against
    the baseline always drifts. On score ties the earlier candidate
    (reference first) wins.
    """
    candidates: list[tuple[str, dict[str, Any]]] = [("reference", reference)]
    candidates.extend((f"variant:{i}", v) for i, v in enumerate(variants or []))
    format_changed = (
        current_format is not None and baseline_format is not None and current_format != baseline_format
    )

    scored: list[DriftComparison] = []
    for label, candidate in candidates:
        components = _components(current, candidate)
        similarity = round(
            WEIGHT_FIELD_SET * components["field_set"]
            + WEIGHT_FIELD_ORDER * components["field_order"]
            + WEIGHT_FIELD_COUNT * components["field_count"]
            + WEIGHT_FIELD_TYPES * components["field_types"],
            SCORE_PRECISION,
        )
        differences = _differences(current, candidate)
        scored.append(
            DriftComparison(
                similarity=similarity,
                is_drift=False,
                matched=label,
                components=components,
                differences=differences,
                critical_changes=_critical_changes(candidate, differences, critical_fields or {}),
            )
        )

    accepting = [
        c for c in scored if not format_changed and c.similarity >= threshold and not c.critical_changes
    ]
    best = _best(accepting) if accepting else _best(scored)
    if format_changed:
        best.differences["format_changed"] = {"baseline": baseline_format, "current": current_format}
    if not accepting:
        best.is_drift = True
        if format_changed:
            best.decision_reasons.append(REASON_FORMAT_CHANGED)
        if best.similarity < threshold:
            best.decision_reasons.append(REASON_SIMILARITY_BELOW_THRESHOLD)
        if best.critical_changes:
            best.decision_reasons.append(REASON_CRITICAL_FIELD_CHANGED)
    return best


def structural_differences(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Field-level differences between two fingerprints (added/removed
    fields, count, order, type changes), without scoring."""
    return _differences(current, baseline)


def _best(comparisons: list[DriftComparison]) -> DriftComparison:
    best = comparisons[0]
    for c in comparisons[1:]:
        if c.similarity > best.similarity:  # strictly greater: earlier candidate wins ties
            best = c
    return best


def _critical_changes(
    baseline: dict[str, Any], differences: dict[str, Any], critical_fields: dict[str, str | None]
) -> list[dict[str, Any]]:
    """Critical fields the baseline structure had that are now missing or
    whose (non-null) type changed. Additions are never critical."""
    changes: list[dict[str, Any]] = []
    removed = set(differences.get("removed_fields") or [])
    type_changes = differences.get("type_changes") or {}
    for name in sorted(critical_fields):
        if name not in set(baseline.get("field_set") or []):
            continue
        if name in removed:
            changes.append({"field": name, "target": critical_fields[name], "change": "removed"})
        elif name in type_changes:
            changes.append(
                {
                    "field": name,
                    "target": critical_fields[name],
                    "change": "type_changed",
                    "baseline_type": type_changes[name]["baseline"],
                    "current_type": type_changes[name]["current"],
                }
            )
    return changes


def _components(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    cur_set = set(current.get("field_set") or [])
    base_set = set(baseline.get("field_set") or [])
    union = cur_set | base_set
    field_set_score = len(cur_set & base_set) / len(union) if union else 1.0

    cur_order = list(current.get("field_order") or [])
    base_order = list(baseline.get("field_order") or [])
    order_score = SequenceMatcher(None, base_order, cur_order, autojunk=False).ratio()

    cur_count = int(current.get("field_count") or 0)
    base_count = int(baseline.get("field_count") or 0)
    count_score = min(cur_count, base_count) / max(cur_count, base_count) if max(cur_count, base_count) else 1.0

    return {
        "field_set": round(field_set_score, SCORE_PRECISION),
        "field_order": round(order_score, SCORE_PRECISION),
        "field_count": round(count_score, SCORE_PRECISION),
        "field_types": round(_type_score(current, baseline), SCORE_PRECISION),
    }


def _comparable_type_pairs(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Shared fields whose type is known and non-null on both sides.
    Null is compatible with any type (optional/absent-valued fields such
    as syslog proc_id), and a fingerprint without field_types (stored
    before Phase 5) contributes no type signal at all."""
    cur_types = current.get("field_types")
    base_types = baseline.get("field_types")
    if not isinstance(cur_types, dict) or not isinstance(base_types, dict):
        return {}
    pairs: dict[str, tuple[str, str]] = {}
    for name in cur_types.keys() & base_types.keys():
        b, c = base_types[name], cur_types[name]
        if b == "null" or c == "null":
            continue
        pairs[name] = (b, c)
    return pairs


def _type_score(current: dict[str, Any], baseline: dict[str, Any]) -> float:
    pairs = _comparable_type_pairs(current, baseline)
    if not pairs:
        return 1.0
    return sum(1 for b, c in pairs.values() if b == c) / len(pairs)


def _differences(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    cur_set = set(current.get("field_set") or [])
    base_set = set(baseline.get("field_set") or [])
    common = cur_set & base_set
    base_common_order = [f for f in baseline.get("field_order") or [] if f in common]
    cur_common_order = [f for f in current.get("field_order") or [] if f in common]
    return {
        "added_fields": sorted(cur_set - base_set),
        "removed_fields": sorted(base_set - cur_set),
        "field_count": {
            "baseline": int(baseline.get("field_count") or 0),
            "current": int(current.get("field_count") or 0),
        },
        "order_changed": base_common_order != cur_common_order,
        "type_changes": {
            name: {"baseline": b, "current": c}
            for name, (b, c) in sorted(_comparable_type_pairs(current, baseline).items())
            if b != c
        },
    }
