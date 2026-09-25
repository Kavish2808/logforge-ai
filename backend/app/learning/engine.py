"""Deterministic learning engine: approved drift -> minimal adapter delta.

Inputs are all stored evidence: the current adapter version, the Phase 5
drift differences, and field evidence computed from the drifted and the
historical events (parsed with the adapter's own parser). Every proposed
change cites observable evidence; nothing is inferred without it.

Learning modes:
- FIELD_ADDITION     map a new field when its name follows a known convention
                     and its values fit the target; otherwise keep it in
                     extensions (unresolved, not an error).
- FIELD_REMOVAL      keep the existing mapping (historical meaning is never
                     deleted); record the field as optional.
- SEMANTIC_REMAP     a removed mapped field and an added field with the same
                     value class whose name follows the same target's naming
                     convention (HIGH) or occupies the same position (MEDIUM)
                     -> the new field inherits the meaning.
- FIELD_TYPE_CHANGE  keep the mapping only if the new values still fit the
                     target/coercion; otherwise stop mapping it (extensions)
                     and require a human decision.
- FIELD_ORDER_CHANGE never changes mappings.
- FORMAT_DRIFT       never learned automatically; needs review.
"""
from __future__ import annotations

from typing import Any

from app.learning.delta import (
    AMBIGUOUS,
    MANUAL_MAPPING_REQUIRED,
    PRESERVED_IN_EXTENSIONS,
    LearnedMapping,
    LearningDelta,
    MappingRemoval,
    SemanticRemap,
    UnresolvedField,
    current_mappings,
)
from app.onboarding.analysis import _field_evidence
from app.onboarding.proposal import SPECIAL_TARGETS
from app.onboarding.providers import _PORT_TARGETS, _is_port_range, _rule_for
from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.declarative import DeclarativeParser
from app.pipeline.parsers.registry import get_parser
from app.schema.adapter import AdapterMapping

FIELD_ADDITION = "FIELD_ADDITION"
FIELD_REMOVAL = "FIELD_REMOVAL"
FIELD_TYPE_CHANGE = "FIELD_TYPE_CHANGE"
FIELD_ORDER_CHANGE = "FIELD_ORDER_CHANGE"
SEMANTIC_REMAP = "SEMANTIC_REMAP"
FORMAT_DRIFT = "FORMAT_DRIFT"

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "LOW", "MEDIUM", "HIGH"


def parse_with_adapter(adapter: AdapterMapping, raw: str) -> dict[str, Any] | None:
    """Parser-level fields of a raw log using the adapter's own parser."""
    parser = DeclarativeParser(adapter.parser) if adapter.parser is not None else get_parser(adapter.format)
    if parser is None:
        return None
    try:
        return parser.parse(raw).fields
    except ParserError:
        return None


def field_evidence(adapter: AdapterMapping, raws: list[str]) -> tuple[dict[str, dict[str, Any]], int]:
    records = [f for f in (parse_with_adapter(adapter, r) for r in raws) if f is not None]
    return _field_evidence(records), len(records)


def build_delta(
    adapter: AdapterMapping,
    differences: dict[str, Any],
    change_types: list[str],
    drifted: dict[str, dict[str, Any]],
    drifted_count: int,
    historical: dict[str, dict[str, Any]],
    critical_fields: dict[str, str | None],
) -> dict[str, Any]:
    """Returns {"delta", "modes", "risk", "risk_reasons"}."""
    added = list(differences.get("added_fields") or [])
    removed = list(differences.get("removed_fields") or [])
    type_changes: dict[str, dict[str, str]] = differences.get("type_changes") or {}
    modes = [t for t in change_types if t in (FIELD_ADDITION, FIELD_REMOVAL, FIELD_TYPE_CHANGE, FIELD_ORDER_CHANGE, FORMAT_DRIFT)]
    delta = LearningDelta()
    risk_reasons: list[str] = []

    if FORMAT_DRIFT in change_types or differences.get("format_changed"):
        delta.unresolved.append(UnresolvedField(
            field="<format>", kind=MANUAL_MAPPING_REQUIRED,
            reason="The log format itself changed; format drift is never learned automatically.",
        ))
        return {"delta": delta, "modes": modes, "risk": RISK_HIGH,
                "risk_reasons": ["Format drift cannot be represented as a safe mapping delta."]}

    mapped = current_mappings(adapter)
    consumed: set[str] = set()
    taken = {m["target"] for f, m in mapped.items() if f not in removed}

    # --- removals: semantic remap when evidence supports it, else keep + optional
    for field in removed:
        if field not in mapped:
            delta.notes.append(f"'{field}' was not mapped; its absence changes nothing.")
            continue
        target = mapped[field]["target"]
        old_class = (historical.get(field) or {}).get("dominant_class")
        candidates = [a for a in added if a not in consumed and (drifted.get(a) or {}).get("dominant_class") == old_class]
        by_name = [a for a in candidates if (_rule_for(a) or (None,))[0] == target]
        old_pos = set((historical.get(field) or {}).get("positions") or [])
        by_position = [a for a in candidates if old_pos & set((drifted.get(a) or {}).get("positions") or [])]
        critical = field in critical_fields
        if len(by_name) == 1:
            new = by_name[0]
            confidence = "HIGH"
            why = f"'{new}' follows the {target} naming convention"
        elif not by_name and len(by_position) == 1 and old_class not in (None, "string"):
            new = by_position[0]
            confidence = "MEDIUM"
            why = f"'{new}' occupies the same position as '{field}'"
        else:
            if len(by_name) > 1 or len(by_position) > 1:
                delta.unresolved.append(UnresolvedField(
                    field=field, kind=AMBIGUOUS,
                    reason=f"Several added fields could replace '{field}' ({', '.join(sorted(by_name or by_position))}).",
                ))
            delta.optional_fields.append(field)
            delta.notes.append(f"'{field}' is absent in the evolved structure; its mapping to {target} is kept (optional).")
            if critical:
                risk_reasons.append(f"Critical field '{field}' ({target}) is absent and no replacement is evidenced.")
            continue
        consumed.add(new)
        n = (drifted.get(new) or {}).get("present_in", 0)
        delta.remaps.append(SemanticRemap(
            from_field=field, to_field=new, target=target, type=mapped[field]["type"], confidence=confidence,
            evidence=[
                f"'{field}' ({target}) disappeared and '{new}' appeared in the same approved drift",
                f"both carry {old_class} values; '{new}' observed in {n}/{drifted_count} drifted samples",
                why,
            ],
        ))
        if critical:
            risk_reasons.append(f"Critical field '{field}' ({target}) is remapped to '{new}'.")

    # --- additions
    for field in added:
        if field in consumed:
            continue
        ev = drifted.get(field) or {}
        present = ev.get("present_in", 0)
        rule = _rule_for(field)
        target = rule[0] if rule else None
        classes = rule[2] if rule else None
        reason = None
        if target is None:
            reason = "no evidence-backed mapping (name matches no known convention)"
        elif target in taken:
            reason = f"target {target} is already mapped by a field that is still present"
        elif classes is not None and ev.get("dominant_class") not in classes:
            reason = f"name suggests {target} but values are {ev.get('dominant_class')}"
        elif target in _PORT_TARGETS and not _is_port_range(ev):
            reason = f"name suggests {target} but values are outside the port range"
        if reason:
            delta.unresolved.append(UnresolvedField(field=field, kind=PRESERVED_IN_EXTENSIONS, reason=f"{reason}; preserved in extensions"))
            continue
        taken.add(target)
        full = present == drifted_count and drifted_count > 0
        delta.add_mappings.append(LearnedMapping(
            raw_field=field, target=target, type=None if target in SPECIAL_TARGETS else rule[3],
            confidence="HIGH" if full else "MEDIUM",
            evidence=[
                f"'{field}' observed in {present}/{drifted_count} drifted samples",
                f"values: {ev.get('dominant_class')}" + (" (type consistent)" if ev.get("type_consistent") else " (mixed types)"),
                f"name follows the {target} naming convention",
                f"{target} is not mapped by adapter v{adapter.version}",
            ],
        ))

    # --- type changes of mapped fields
    for field, change in sorted(type_changes.items()):
        if field not in mapped:
            continue
        m = mapped[field]
        new_class = (drifted.get(field) or {}).get("dominant_class")
        if _compatible(m["target"], m["type"], new_class):
            delta.notes.append(
                f"'{field}' changed type {change['baseline']} → {change['current']}, but its values ({new_class}) "
                f"still fit {m['target']}{' with ' + m['type'] + ' coercion' if m['type'] else ''}; mapping kept."
            )
            continue
        delta.remove_mappings.append(MappingRemoval(
            raw_field=field, target=m["target"],
            reason=f"values are now {new_class}, which no longer fits {m['target']}; kept in extensions until a human maps it",
        ))
        delta.unresolved.append(UnresolvedField(
            field=field, kind=MANUAL_MAPPING_REQUIRED,
            reason=f"meaning of '{field}' became uncertain ({change['baseline']} → {change['current']}); manual mapping required",
        ))
        risk_reasons.append(f"Mapped field '{field}' ({m['target']}) changed type incompatibly.")

    if delta.remaps:
        modes.append(SEMANTIC_REMAP)
    if FIELD_ORDER_CHANGE in modes and not delta.changes_mapping():
        delta.notes.append("Only the field order changed; key-value mappings are order-independent, nothing to learn.")

    if risk_reasons or delta.remove_mappings or any(u.kind != PRESERVED_IN_EXTENSIONS for u in delta.unresolved):
        risk = RISK_HIGH
    elif delta.optional_fields or any(x.confidence != "HIGH" for x in [*delta.add_mappings, *delta.remaps]):
        risk = RISK_MEDIUM
    else:
        risk = RISK_LOW
    return {"delta": delta, "modes": modes, "risk": risk, "risk_reasons": risk_reasons}


def _compatible(target: str, coercion: str | None, new_class: str | None) -> bool:
    if new_class in (None, "null", "empty"):
        return True
    if target in ("network.src_ip", "network.dst_ip"):
        return new_class in ("ipv4", "ipv6")
    if coercion == "int":
        return new_class == "integer"
    if coercion == "float":
        return new_class in ("integer", "number")
    if coercion == "bool":
        return new_class == "boolean"
    if target == "timestamp":
        return new_class in ("timestamp", "epoch")
    return new_class not in ("object", "array")
