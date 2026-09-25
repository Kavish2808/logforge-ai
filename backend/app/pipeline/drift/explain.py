"""Human-readable drift explanation, derived purely from the structured
drift record (processing_metadata.drift). Deterministic template
rendering — no LLM, no free-form generation. The structured record is
the source of truth; this text is regenerated from it on every read.

Accepts either the stored dict or the DriftMetadata model (attribute access).
"""
from __future__ import annotations

from typing import Any

_HEADERS = {
    "DRIFT": "DRIFT DETECTED",
    "POSSIBLE_FORMAT_DRIFT": "POSSIBLE FORMAT DRIFT",
    "NORMAL": "NO DRIFT (NORMAL)",
    "BASELINE_CREATED": "BASELINE CREATED",
    "ERROR": "DRIFT EVALUATION ERROR",
}


def render_explanation(record: Any) -> str:
    status = _str(_get(record, "status"))
    lines = [_HEADERS.get(status, status)]
    if status == "POSSIBLE_FORMAT_DRIFT":
        lines.append(f"Previously known source: {_get(record, 'source_key')}")
        lines.append(f"Current adapter: {_get(record, 'current_adapter')}")
    else:
        lines.append(f"Source: {_get(record, 'source_key')}")

    if status == "ERROR":
        lines.append(f"Error: {_get(record, 'error')}")
        lines.append("The event was persisted with the status the pipeline assigned; no drift decision was made.")
        return "\n".join(lines)

    version = _get(record, "baseline_version")
    if version is not None:
        origin = _get(record, "baseline_origin")
        lines.append(f"Baseline Version: {version}" + (f" ({origin})" if origin else ""))
    matched = _get(record, "matched")
    if matched and status in ("NORMAL", "DRIFT"):
        lines.append(f"Matched: {matched}")
    similarity = _get(record, "similarity")
    if similarity is not None:
        label = "Similarity to known source baseline" if status == "POSSIBLE_FORMAT_DRIFT" else "Similarity"
        lines.append(f"{label}: {similarity:.4f}")
    threshold = _get(record, "threshold")
    if threshold is not None:
        lines.append(f"Threshold: {threshold}")
    severity = _get(record, "severity")
    if severity:
        lines.append(f"Severity: {_str(severity)} (score {_get(record, 'severity_score')})")
    change_types = _get(record, "change_types") or []
    if change_types:
        lines.append("Change types: " + ", ".join(_str(t) for t in change_types))
    reasons = _get(record, "decision_reasons") or []
    if reasons:
        lines.append("Decision: " + ", ".join(_str(r) for r in reasons))

    changes = _change_lines(record)
    if changes:
        lines.append("")
        lines.append("Changes:")
        lines.extend(changes)

    evidence = _get(record, "evidence") or []
    if evidence:
        lines.append("")
        lines.append("Evidence:")
        for item in evidence:
            lines.append(f"* {_get(item, 'reason')}: {_get(item, 'detail')}")

    if status in ("DRIFT", "POSSIBLE_FORMAT_DRIFT"):
        lines.append("")
        lines.append("Recommendation:")
        actions = _get(record, "recommended_actions") or []
        if actions:
            lines.append(", ".join(_str(a) for a in actions))
        review = _get(record, "review")
        if review:
            lines.append(
                f"Reviewed: {_get(review, 'resolution')} at {_get(review, 'reviewed_at')} "
                f"(baseline v{_get(review, 'baseline_version')})."
            )
        elif _get(record, "reonboarding_required"):
            lines.append("Human review required.")
    return "\n".join(lines)


def _change_lines(record: Any) -> list[str]:
    diff = _get(record, "differences")
    lines: list[str] = []
    if diff:
        lines.extend(f"+ {name}" for name in _get(diff, "added_fields") or [])
        lines.extend(f"- {name}" for name in _get(diff, "removed_fields") or [])
        for name, change in sorted((_get(diff, "type_changes") or {}).items()):
            lines.append(f"~ {name} type changed {_get(change, 'baseline')} → {_get(change, 'current')}")
        if _get(diff, "order_changed"):
            lines.append("↕ field order changed")
        fmt = _get(diff, "format_changed")
        if fmt:
            lines.append(f"⇄ format changed {_get(fmt, 'baseline')} → {_get(fmt, 'current')}")
    for change in _get(record, "critical_field_changes") or []:
        target = _get(change, "target")
        label = f"{_get(change, 'field')}" + (f" ({target})" if target else "")
        if _get(change, "change") == "removed":
            lines.append(f"! critical field removed: {label}")
        else:
            lines.append(
                f"! critical field type changed: {label} "
                f"{_get(change, 'baseline_type')} → {_get(change, 'current_type')}"
            )
    return lines


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _str(value: Any) -> str:
    # StrEnum members and plain strings render the same.
    return str(value.value) if hasattr(value, "value") else str(value)
