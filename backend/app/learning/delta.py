"""The learning delta: the minimal, declarative change from the current
adapter version to the proposed one.

A delta is untrusted data (whether produced by the deterministic engine, an
LLM assistant, or a human). It is strictly schema-validated (unknown keys
rejected, no field can carry code or a regex), then reviewed against the
drift evidence so it can only touch fields the approved drift actually
changed, and only map to allow-listed universal-schema targets.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.onboarding.proposal import ALLOWED_TARGETS, SPECIAL_TARGETS
from app.schema.adapter import FIELD_NAME_RE, AdapterMapping, FieldMapEntry

Confidence = Literal["HIGH", "MEDIUM", "LOW"]
CoercionType = Literal["str", "int", "float", "bool"]
MAX_ITEMS = 100
MAX_TEXT = 300

# Unresolved-item kinds.
PRESERVED_IN_EXTENSIONS = "PRESERVED_IN_EXTENSIONS"  # no evidence-backed mapping; the field is kept in extensions
MANUAL_MAPPING_REQUIRED = "MANUAL_MAPPING_REQUIRED"  # meaning became uncertain; a human must decide
AMBIGUOUS = "AMBIGUOUS"  # several equally plausible interpretations


def _field_name(value: str) -> str:
    if not FIELD_NAME_RE.match(value) and value != "@timestamp":
        raise ValueError(f"invalid raw field name {value!r}")
    return value


def _bounded(items: list[str]) -> list[str]:
    return [item[:MAX_TEXT] for item in items]


class LearnedMapping(BaseModel):
    """A new mapping for a field that appeared in the evolved structure."""

    model_config = ConfigDict(extra="forbid")

    raw_field: str = Field(..., max_length=64)
    target: str = Field(..., max_length=64)
    type: CoercionType | None = None
    confidence: Confidence
    evidence: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("raw_field")
    @classmethod
    def _valid_raw_field(cls, value: str) -> str:
        return _field_name(value)

    @field_validator("evidence")
    @classmethod
    def _bounded_evidence(cls, value: list[str]) -> list[str]:
        return _bounded(value)


class SemanticRemap(BaseModel):
    """`to_field` carries the same meaning `from_field` carried (a rename).
    The target never changes — a remap preserves meaning, it does not invent one."""

    model_config = ConfigDict(extra="forbid")

    from_field: str = Field(..., max_length=64)
    to_field: str = Field(..., max_length=64)
    target: str = Field(..., max_length=64)
    type: CoercionType | None = None
    confidence: Confidence
    evidence: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("from_field", "to_field")
    @classmethod
    def _valid_fields(cls, value: str) -> str:
        return _field_name(value)

    @field_validator("evidence")
    @classmethod
    def _bounded_evidence(cls, value: list[str]) -> list[str]:
        return _bounded(value)


class MappingRemoval(BaseModel):
    """Stop mapping a field whose meaning became uncertain (it is then
    preserved in extensions, never dropped)."""

    model_config = ConfigDict(extra="forbid")

    raw_field: str = Field(..., max_length=64)
    target: str = Field(..., max_length=64)
    reason: str = Field(..., max_length=MAX_TEXT)


class UnresolvedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., max_length=64)
    kind: Literal["PRESERVED_IN_EXTENSIONS", "MANUAL_MAPPING_REQUIRED", "AMBIGUOUS"]
    reason: str = Field(..., max_length=MAX_TEXT)


class LearningDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add_mappings: list[LearnedMapping] = Field(default_factory=list, max_length=MAX_ITEMS)
    remaps: list[SemanticRemap] = Field(default_factory=list, max_length=MAX_ITEMS)
    remove_mappings: list[MappingRemoval] = Field(default_factory=list, max_length=MAX_ITEMS)
    optional_fields: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    unresolved: list[UnresolvedField] = Field(default_factory=list, max_length=MAX_ITEMS)
    notes: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("optional_fields")
    @classmethod
    def _valid_optional(cls, value: list[str]) -> list[str]:
        return [_field_name(v) for v in value]

    @field_validator("notes")
    @classmethod
    def _bounded_notes(cls, value: list[str]) -> list[str]:
        return _bounded(value)

    def changes_mapping(self) -> bool:
        return bool(self.add_mappings or self.remaps or self.remove_mappings or self.optional_fields)

    def needs_manual_mapping(self) -> bool:
        return any(u.kind != PRESERVED_IN_EXTENSIONS for u in self.unresolved)


def current_mappings(adapter: AdapterMapping) -> dict[str, dict[str, Any]]:
    """raw field -> {"target", "type"} for every mapping of an adapter,
    including the special (event_action/severity/timestamp/product_version) ones."""
    mappings = {
        raw: {"target": entry.target, "type": entry.type} for raw, entry in adapter.resolved_field_map().items()
    }
    for target, attr in SPECIAL_TARGETS.items():
        raw = getattr(adapter, attr)
        if raw:
            mappings[raw] = {"target": target, "type": None}
    return mappings


def review_delta(
    delta: LearningDelta,
    adapter: AdapterMapping,
    differences: dict[str, Any],
    observed_fields: set[str],
) -> list[str]:
    """Deterministic safety review. Any issue makes the proposal REJECTED:
    it may only touch fields the approved drift changed, only map observed
    fields, only use allow-listed targets, and never change the meaning of
    an existing mapping."""
    issues: list[str] = []
    added = set(differences.get("added_fields") or [])
    removed = set(differences.get("removed_fields") or [])
    type_changed = set((differences.get("type_changes") or {}).keys())
    mapped = current_mappings(adapter)
    removed_targets = {mapped[r.raw_field]["target"] for r in delta.remove_mappings if r.raw_field in mapped}
    taken = {m["target"] for f, m in mapped.items() if f not in removed} - removed_targets
    seen_fields: set[str] = set()

    def claim(field: str, where: str) -> None:
        if field in seen_fields:
            issues.append(f"{where}: field '{field}' is changed more than once.")
        seen_fields.add(field)

    for m in delta.add_mappings:
        claim(m.raw_field, "add_mappings")
        if m.target not in ALLOWED_TARGETS:
            issues.append(f"add_mappings: target '{m.target}' is not in the universal schema allow-list.")
        if m.raw_field not in added:
            issues.append(f"add_mappings: '{m.raw_field}' is not a field the approved drift added (unrelated change).")
        elif m.raw_field not in observed_fields:
            issues.append(f"add_mappings: '{m.raw_field}' was not observed in the drifted evidence.")
        if m.raw_field in mapped:
            issues.append(f"add_mappings: '{m.raw_field}' is already mapped.")
        if m.target in taken:
            issues.append(f"add_mappings: target '{m.target}' is already mapped by a field that is still present.")
        if m.type is not None and m.target in SPECIAL_TARGETS:
            issues.append(f"add_mappings: type coercion is not supported for special target '{m.target}'.")
        taken.add(m.target)

    for r in delta.remaps:
        claim(r.to_field, "remaps")
        if r.from_field not in mapped:
            issues.append(f"remaps: '{r.from_field}' is not mapped by the current adapter.")
        elif mapped[r.from_field]["target"] != r.target:
            issues.append(
                f"remaps: '{r.from_field}' maps to '{mapped[r.from_field]['target']}', not '{r.target}' "
                "(a remap may not change meaning)."
            )
        if r.from_field not in removed:
            issues.append(f"remaps: '{r.from_field}' was not removed by the approved drift.")
        if r.to_field not in added:
            issues.append(f"remaps: '{r.to_field}' is not a field the approved drift added (unrelated change).")
        elif r.to_field not in observed_fields:
            issues.append(f"remaps: '{r.to_field}' was not observed in the drifted evidence.")

    for rm in delta.remove_mappings:
        claim(rm.raw_field, "remove_mappings")
        if rm.raw_field not in mapped:
            issues.append(f"remove_mappings: '{rm.raw_field}' is not mapped by the current adapter.")
        if rm.raw_field not in type_changed:
            issues.append(f"remove_mappings: '{rm.raw_field}' did not change type in the approved drift (unrelated change).")

    for field in delta.optional_fields:
        if field not in removed:
            issues.append(f"optional_fields: '{field}' was not removed by the approved drift.")
    return issues


def apply_delta(adapter: AdapterMapping, delta: LearningDelta, *, version: int, description: str) -> AdapterMapping:
    """Current adapter + delta -> the proposed adapter version (validated by
    the same model as every shipped/onboarded adapter).

    A semantic remap to a field_map target *adds* the new field as an alias
    and keeps the old mapping, so historical logs keep normalizing exactly as
    before. A special target (event_action, severity, ...) holds a single raw
    field, so remapping it replaces the old field — the compatibility check
    in the learning sandbox then decides whether that is acceptable."""
    mappings = current_mappings(adapter)
    for removal in delta.remove_mappings:
        mappings.pop(removal.raw_field, None)
    optional = set(adapter.optional_fields) | set(delta.optional_fields)
    for remap in delta.remaps:
        previous = mappings.get(remap.from_field) or {}
        if remap.target in SPECIAL_TARGETS:
            mappings.pop(remap.from_field, None)
        else:
            optional.add(remap.from_field)
        mappings[remap.to_field] = {"target": remap.target, "type": remap.type or previous.get("type")}
    for m in delta.add_mappings:
        mappings[m.raw_field] = {"target": m.target, "type": m.type}

    field_map: dict[str, FieldMapEntry] = {}
    special: dict[str, str | None] = {attr: None for attr in SPECIAL_TARGETS.values()}
    for raw, m in mappings.items():
        if m["target"] in SPECIAL_TARGETS:
            special[SPECIAL_TARGETS[m["target"]]] = raw
        else:
            field_map[raw] = FieldMapEntry(target=m["target"], type=m["type"])

    data = adapter.model_dump()
    data.update(
        version=str(version),
        description=description,
        field_map=field_map,
        optional_fields=sorted(optional),
        source="onboarded",
        **special,
    )
    return AdapterMapping.model_validate(data)


def mapping_diff(current: AdapterMapping, proposed: AdapterMapping) -> dict[str, Any]:
    """What mapping will change / remain unchanged between two versions."""
    old, new = current_mappings(current), current_mappings(proposed)
    return {
        "added": sorted(f"{f} → {new[f]['target']}" for f in new.keys() - old.keys()),
        "removed": sorted(f"{f} → {old[f]['target']}" for f in old.keys() - new.keys()),
        "changed": sorted(
            f"{f}: {old[f]['target']}{'/' + old[f]['type'] if old[f]['type'] else ''} → "
            f"{new[f]['target']}{'/' + new[f]['type'] if new[f]['type'] else ''}"
            for f in old.keys() & new.keys() if old[f] != new[f]
        ),
        "unchanged": sorted(f"{f} → {new[f]['target']}" for f in old.keys() & new.keys() if old[f] == new[f]),
        "optional_fields": sorted(set(proposed.optional_fields) - set(current.optional_fields)),
    }


def normalized_mapping(adapter_json: dict[str, Any]) -> dict[str, Any]:
    """Canonical comparable form of a stored adapter mapping (ignores
    version/description; tolerant of rows stored before optional fields existed)."""
    data = AdapterMapping.model_validate(adapter_json).model_dump(mode="json")
    data.pop("version", None)
    data.pop("description", None)
    return data
