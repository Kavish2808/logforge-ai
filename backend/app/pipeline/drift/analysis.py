"""Deterministic drift intelligence on top of the structural comparator.

Everything here is a pure function of structural evidence (fingerprint
differences, adapter YAML, the raw log text): no DB, no network, no ML,
no LLM. The comparator decides NORMAL vs DRIFT; this module explains and
grades that decision:

- classify_changes      -> which kinds of structural change occurred
- compute_severity      -> LOW / MEDIUM / HIGH / CRITICAL from a points formula
- resolve_critical_fields -> which raw fields are critical for an adapter
- recommend_actions     -> what a human reviewer should look at
- fallback_evidence     -> why a generic-adapter event may really be a known
                           vendor source whose format changed
"""
from __future__ import annotations

import math
from typing import Any

from app.schema.adapter import AdapterMapping

# --------------------------------------------------------------------------
# Change classification
# --------------------------------------------------------------------------

FIELD_ADDITION = "FIELD_ADDITION"
FIELD_REMOVAL = "FIELD_REMOVAL"
FIELD_TYPE_CHANGE = "FIELD_TYPE_CHANGE"
FIELD_ORDER_CHANGE = "FIELD_ORDER_CHANGE"
FORMAT_DRIFT = "FORMAT_DRIFT"
MULTIPLE_STRUCTURAL_CHANGE = "MULTIPLE_STRUCTURAL_CHANGE"


def classify_changes(differences: dict[str, Any] | None, *, format_drift: bool = False) -> list[str]:
    """Every applicable change type, in a fixed order. MULTIPLE_STRUCTURAL_CHANGE
    is appended when two or more distinct kinds of change occurred together."""
    diff = differences or {}
    types: list[str] = []
    if diff.get("added_fields"):
        types.append(FIELD_ADDITION)
    if diff.get("removed_fields"):
        types.append(FIELD_REMOVAL)
    if diff.get("type_changes"):
        types.append(FIELD_TYPE_CHANGE)
    if diff.get("order_changed"):
        types.append(FIELD_ORDER_CHANGE)
    if format_drift or diff.get("format_changed"):
        types.append(FORMAT_DRIFT)
    if len(types) >= 2:
        types.append(MULTIPLE_STRUCTURAL_CHANGE)
    return types


# --------------------------------------------------------------------------
# Severity — MVP heuristic points formula (deterministic, documented)
# --------------------------------------------------------------------------
#
#   score = 1 x added fields
#         + 2 x removed fields              (a mapped field may have been lost)
#         + 4 x type-changed fields         (breaks typed mapping/coercion)
#         + 1 if field order changed
#         + floor(10 x (1 - field-set Jaccard))   (overall structural magnitude)
#         + 6 x critical fields removed / type-changed
#         + 9 if the event fell back to a generic adapter (possible format drift)
#         + 15 if the log format itself changed
#
#   LOW < 4 <= MEDIUM < 9 <= HIGH < 15 <= CRITICAL
#   Floors: any critical-field change is at least HIGH; a format change is CRITICAL.
#
# Severity is deliberately NOT derived from the similarity score: two drifts
# with equal similarity can differ greatly in impact (e.g. an added optional
# field vs. a removed source IP).

SEVERITY_POINTS_ADDED_FIELD = 1
SEVERITY_POINTS_REMOVED_FIELD = 2
SEVERITY_POINTS_TYPE_CHANGE = 4
SEVERITY_POINTS_ORDER_CHANGE = 1
SEVERITY_MAGNITUDE_SCALE = 10
SEVERITY_POINTS_CRITICAL_FIELD = 6
SEVERITY_POINTS_ADAPTER_FALLBACK = 9
SEVERITY_POINTS_FORMAT_CHANGE = 15

SEVERITY_MEDIUM_MIN = 4
SEVERITY_HIGH_MIN = 9
SEVERITY_CRITICAL_MIN = 15

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
CRITICAL = "CRITICAL"
_SEVERITY_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}


def compute_severity(
    differences: dict[str, Any] | None,
    components: dict[str, float] | None,
    critical_changes: list[dict[str, Any]] | None,
    *,
    adapter_fallback: bool = False,
    format_changed: bool = False,
) -> tuple[str, int, dict[str, int]]:
    """Returns (level, score, factors). `factors` lists every non-zero
    contribution so the grade is fully explainable."""
    diff = differences or {}
    factors: dict[str, int] = {
        "added_fields": SEVERITY_POINTS_ADDED_FIELD * len(diff.get("added_fields") or []),
        "removed_fields": SEVERITY_POINTS_REMOVED_FIELD * len(diff.get("removed_fields") or []),
        "type_changes": SEVERITY_POINTS_TYPE_CHANGE * len(diff.get("type_changes") or {}),
        "order_change": SEVERITY_POINTS_ORDER_CHANGE if diff.get("order_changed") else 0,
        "structural_magnitude": _magnitude_points(components),
        "critical_fields": SEVERITY_POINTS_CRITICAL_FIELD * len(critical_changes or []),
        "adapter_fallback": SEVERITY_POINTS_ADAPTER_FALLBACK if adapter_fallback else 0,
        "format_change": SEVERITY_POINTS_FORMAT_CHANGE if format_changed else 0,
    }
    factors = {k: v for k, v in factors.items() if v}
    score = sum(factors.values())

    level = severity_for_score(score)
    if critical_changes:
        level = _max_level(level, HIGH)
    if format_changed:
        level = CRITICAL
    return level, score, factors


def severity_for_score(score: int) -> str:
    if score >= SEVERITY_CRITICAL_MIN:
        return CRITICAL
    if score >= SEVERITY_HIGH_MIN:
        return HIGH
    if score >= SEVERITY_MEDIUM_MIN:
        return MEDIUM
    return LOW


def _magnitude_points(components: dict[str, float] | None) -> int:
    if not components or "field_set" not in components:
        return 0
    # Small epsilon guards against float representation (e.g. 10*(1-0.9) = 0.99999...).
    return math.floor(SEVERITY_MAGNITUDE_SCALE * (1.0 - components["field_set"]) + 1e-9)


def _max_level(a: str, b: str) -> str:
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


# --------------------------------------------------------------------------
# Critical fields
# --------------------------------------------------------------------------

# OCSF-level targets resolvable through any adapter: the two "special"
# adapter fields, or any field_map target.
_SPECIAL_TARGETS = {
    "event_action": "event_action_field",
    "severity": "severity_field",
    "timestamp": "timestamp_field",
    "product_version": "product_version_field",
}


def resolve_critical_fields(adapter: AdapterMapping, default_targets: list[str]) -> dict[str, str | None]:
    """Map critical OCSF targets to this adapter's raw (parser-level) field
    names, using the adapter's own YAML (field_map, event_action_field,
    severity_field, ...). Adapter-declared `critical_fields` (raw names) are
    added as-is. Returns {raw_field_name: target_label_or_None}."""
    resolved: dict[str, str | None] = {}
    field_map = adapter.resolved_field_map()
    for target in default_targets:
        attr = _SPECIAL_TARGETS.get(target)
        if attr is not None:
            source = getattr(adapter, attr)
            if source:
                resolved.setdefault(source, target)
            continue
        for source, entry in field_map.items():
            if entry.target == target:
                resolved.setdefault(source, target)
    for source in adapter.critical_fields:
        resolved.setdefault(source, None)
    return resolved


# --------------------------------------------------------------------------
# Re-onboarding recommendations (metadata only — humans decide)
# --------------------------------------------------------------------------

REVIEW_ADDED_FIELDS = "REVIEW_ADDED_FIELDS"
REVIEW_REMOVED_FIELDS = "REVIEW_REMOVED_FIELDS"
REVIEW_TYPE_CHANGES = "REVIEW_TYPE_CHANGES"
REVIEW_FIELD_ORDER = "REVIEW_FIELD_ORDER"
REVIEW_CRITICAL_FIELD_CHANGE = "REVIEW_CRITICAL_FIELD_CHANGE"
REVIEW_FORMAT_DRIFT = "REVIEW_FORMAT_DRIFT"


def recommend_actions(change_types: list[str], critical_changes: list[dict[str, Any]] | None) -> list[str]:
    """Most important first: critical fields and format, then field-level changes."""
    actions: list[str] = []
    if critical_changes:
        actions.append(REVIEW_CRITICAL_FIELD_CHANGE)
    if FORMAT_DRIFT in change_types:
        actions.append(REVIEW_FORMAT_DRIFT)
    if FIELD_REMOVAL in change_types:
        actions.append(REVIEW_REMOVED_FIELDS)
    if FIELD_TYPE_CHANGE in change_types:
        actions.append(REVIEW_TYPE_CHANGES)
    if FIELD_ADDITION in change_types:
        actions.append(REVIEW_ADDED_FIELDS)
    if FIELD_ORDER_CHANGE in change_types:
        actions.append(REVIEW_FIELD_ORDER)
    return actions


# --------------------------------------------------------------------------
# Adapter-fallback (possible format drift) evidence
# --------------------------------------------------------------------------

EVIDENCE_MATCH_FIELD_NEAR_MISS = "MATCH_FIELD_NEAR_MISS"
EVIDENCE_IDENTITY_FIELD_IN_OTHER_FORMAT = "VENDOR_IDENTITY_FIELD_IN_OTHER_FORMAT"
EVIDENCE_VENDOR_SIGNATURE_IN_RAW = "VENDOR_SIGNATURE_IN_RAW"

# A match token shorter than this is too generic to search for in raw text.
MIN_RAW_SIGNATURE_LENGTH = 4
_MAX_OBSERVED_VALUE_LENGTH = 120


def fallback_evidence(
    vendor_adapter: AdapterMapping,
    *,
    event_format: str,
    parsed_fields: dict[str, Any],
    raw_event: str,
) -> list[dict[str, str]]:
    """Deterministic reasons to suspect that an event routed to a generic
    adapter actually comes from `vendor_adapter`'s source. Empty list = no
    evidence. This is only ever *possible* format drift: generic adapters
    legitimately receive unrelated data."""
    rule = vendor_adapter.match
    if rule is None or rule.field is None:
        return []
    token = rule.equals if rule.equals is not None else rule.contains
    if not token:
        return []

    evidence: list[dict[str, str]] = []
    value = parsed_fields.get(rule.field)
    if value is not None and token.lower() in str(value).lower():
        observed = str(value)[:_MAX_OBSERVED_VALUE_LENGTH]
        expected = f"{rule.field} {'equals' if rule.equals is not None else 'contains'} '{token}'"
        if event_format == vendor_adapter.format:
            evidence.append(
                {
                    "reason": EVIDENCE_MATCH_FIELD_NEAR_MISS,
                    "detail": f"Adapter '{vendor_adapter.id}' requires {expected}; observed '{observed}'.",
                }
            )
        else:
            evidence.append(
                {
                    "reason": EVIDENCE_IDENTITY_FIELD_IN_OTHER_FORMAT,
                    "detail": (
                        f"Identity field '{rule.field}' = '{observed}' of adapter '{vendor_adapter.id}' "
                        f"({vendor_adapter.format}) appeared in a {event_format} event."
                    ),
                }
            )
    if len(token) >= MIN_RAW_SIGNATURE_LENGTH and token.lower() in raw_event.lower():
        evidence.append(
            {
                "reason": EVIDENCE_VENDOR_SIGNATURE_IN_RAW,
                "detail": f"Raw log contains the '{vendor_adapter.id}' match signature '{token}'.",
            }
        )
    return evidence
