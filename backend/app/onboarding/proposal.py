"""The adapter proposal: untrusted suggestion -> validated, declarative data.

Whatever produces a proposal (Claude, the offline analyzer, or a human
editing one) is untrusted. A proposal is only ever *data*:

- strict Pydantic schema (unknown keys rejected, enumerated parser
  strategies, bounded sizes) — there is no field that can hold code, a
  regex or an expression;
- every mapping target must be in the allow-list derived from the OCSF
  universal schema;
- every raw field must be supported by sample evidence (no invented fields);
- conversion to an AdapterMapping re-validates through the same model the
  shipped YAML adapters use.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schema.adapter import (
    FIELD_NAME_RE,
    AdapterMapping,
    DeclarativeParserConfig,
    FieldMapEntry,
    MatchRule,
    OCSFClassification,
)
from app.schema.ocsf import NetworkInfo, ProcessInfo, UserInfo

NATIVE_FORMATS = ("syslog", "json", "cef")
DECLARATIVE_FORMATS = ("delimited", "kv")
MAX_MAPPINGS = 200
MAX_TEXT = 500

# Targets resolved through dedicated adapter fields rather than field_map.
SPECIAL_TARGETS = {
    "event_action": "event_action_field",
    "severity": "severity_field",
    "timestamp": "timestamp_field",
    "product_version": "product_version_field",
}
# Typed OCSF groups, derived from the universal event schema itself.
GROUP_TARGETS = {
    *(f"network.{name}" for name in NetworkInfo.model_fields),
    *(f"user.{name}" for name in UserInfo.model_fields),
    *(f"process.{name}" for name in ProcessInfo.model_fields),
}
# Flat normalized_event attributes (the vendor-neutral subset of targets the
# shipped adapters use).
FLAT_TARGETS = {
    "event_message", "device_hostname", "device_id", "action_taken", "policy_id", "rule_name",
    "bytes_sent", "bytes_received", "duration_seconds", "log_type", "log_subtype",
    "src_interface", "dst_interface", "signature_id", "destination_user", "application",
    "session_id", "raw_message",
}
ALLOWED_TARGETS = frozenset(set(SPECIAL_TARGETS) | GROUP_TARGETS | FLAT_TARGETS)

# OCSF classes an onboarded adapter may declare: uid -> (class_name, category_uid, category_name).
OCSF_CLASSES: dict[int, tuple[str, int, str]] = {
    1001: ("System Activity", 1, "System Activity"),
    2004: ("Detection Finding", 2, "Findings"),
    3002: ("Authentication", 3, "Identity & Access Management"),
    4001: ("Network Activity", 4, "Network Activity"),
    4002: ("HTTP Activity", 4, "Network Activity"),
    4003: ("DNS Activity", 4, "Network Activity"),
    6003: ("Application Activity", 6, "Application Activity"),
}

_TIMESTAMP_FORMAT_RE = re.compile(r"^[%A-Za-z0-9 \-:./,+TZ]{1,64}$")
ADAPTER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class ProposedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_field: str = Field(..., max_length=64)
    target: str = Field(..., max_length=64)
    type: Literal["str", "int", "float", "bool"] | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = Field(default="", max_length=MAX_TEXT)

    @field_validator("raw_field")
    @classmethod
    def _raw_field_name(cls, value: str) -> str:
        if not FIELD_NAME_RE.match(value) and value != "@timestamp":
            raise ValueError(f"invalid raw field name {value!r}")
        return value


class ProposedMatch(BaseModel):
    """Identity rule: how future logs are recognized as this source."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., max_length=64)
    equals: str | None = Field(default=None, min_length=1, max_length=128)
    contains: str | None = Field(default=None, min_length=3, max_length=128)

    @model_validator(mode="after")
    def _exactly_one(self) -> "ProposedMatch":
        if (self.equals is None) == (self.contains is None):
            raise ValueError("match needs exactly one of 'equals' or 'contains'")
        return self


class ProposedParser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["native", "delimited", "kv"]
    delimiter: Literal["|", ",", ";", "\t"] | None = None
    columns: list[str] = Field(default_factory=list, max_length=256)
    pair_separator: Literal["whitespace", ",", ";", "|"] = "whitespace"
    kv_separator: Literal["=", ":"] = "="
    min_pairs: int = Field(default=3, ge=1, le=64)

    def to_declarative(self) -> DeclarativeParserConfig | None:
        if self.strategy == "native":
            return None
        return DeclarativeParserConfig(
            strategy=self.strategy,
            delimiter=self.delimiter,
            columns=self.columns,
            pair_separator=self.pair_separator,
            kv_separator=self.kv_separator,
            min_pairs=self.min_pairs,
        )


class AdapterProposal(BaseModel):
    """A suggested adapter for an unknown source. Pure data."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(..., min_length=1, max_length=128)
    product: str = Field(..., min_length=1, max_length=128)
    format: Literal["syslog", "json", "cef", "delimited", "kv"]
    parser: ProposedParser
    match: ProposedMatch
    ocsf_class_uid: int
    event_type: str | None = Field(default=None, max_length=128)
    mappings: list[ProposedMapping] = Field(default_factory=list, max_length=MAX_MAPPINGS)
    severity_map: dict[str, str] = Field(default_factory=dict, max_length=64)
    timestamp_format: str | None = None
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    ambiguous_fields: list[str] = Field(default_factory=list, max_length=MAX_MAPPINGS)

    @field_validator("reasoning", "assumptions")
    @classmethod
    def _bounded_text(cls, items: list[str]) -> list[str]:
        return [item[:MAX_TEXT] for item in items]

    @field_validator("severity_map")
    @classmethod
    def _bounded_severity_map(cls, mapping: dict[str, str]) -> dict[str, str]:
        for key, value in mapping.items():
            if len(key) > 32 or len(value) > 32:
                raise ValueError("severity_map keys and values are limited to 32 characters")
        return mapping

    @field_validator("timestamp_format")
    @classmethod
    def _safe_timestamp_format(cls, value: str | None) -> str | None:
        if value is not None and not _TIMESTAMP_FORMAT_RE.match(value):
            raise ValueError("timestamp_format may only contain strftime directives and separators")
        return value

    @model_validator(mode="after")
    def _format_parser_consistency(self) -> "AdapterProposal":
        if self.format in NATIVE_FORMATS and self.parser.strategy != "native":
            raise ValueError(f"format '{self.format}' is parsed natively; parser.strategy must be 'native'")
        if self.format in DECLARATIVE_FORMATS and self.parser.strategy != self.format:
            raise ValueError(f"format '{self.format}' requires parser.strategy '{self.format}'")
        return self


# --------------------------------------------------------------------------
# Evidence-based review
# --------------------------------------------------------------------------


def review_proposal(proposal: AdapterProposal, analysis: dict[str, Any]) -> dict[str, Any]:
    """Deterministic checks of a schema-valid proposal against the sample
    evidence. Returns {"issues": [...], "accepted": [...], "rejected": [...]}.
    `issues` are proposal-level problems (the proposal cannot be sandboxed);
    `rejected` are individual mappings that were dropped — their raw fields
    stay in extensions."""
    issues: list[str] = []
    evidence_fields = set((analysis.get("fields") or {}).keys())
    dominant = analysis.get("dominant_format")
    hint = analysis.get("parser_hint") or {}

    if proposal.format != dominant:
        issues.append(f"Proposed format '{proposal.format}' contradicts the evidence: samples are '{dominant}'.")
    if proposal.ocsf_class_uid not in OCSF_CLASSES:
        issues.append(f"OCSF class {proposal.ocsf_class_uid} is not an allowed onboarding class {sorted(OCSF_CLASSES)}.")

    if proposal.format == "delimited":
        known_fields = set(proposal.parser.columns)
        observed = hint.get("column_count")
        if observed is not None and len(proposal.parser.columns) != observed:
            issues.append(
                f"Proposed {len(proposal.parser.columns)} columns, but the samples have {observed} delimited columns."
            )
        if hint.get("delimiter") and proposal.parser.delimiter != hint.get("delimiter"):
            issues.append(
                f"Proposed delimiter {proposal.parser.delimiter!r} differs from the observed delimiter {hint['delimiter']!r}."
            )
    else:
        known_fields = evidence_fields

    if proposal.match.field not in known_fields:
        issues.append(f"Match field '{proposal.match.field}' does not occur in the samples.")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    used_targets: set[str] = set()
    used_fields: set[str] = set()
    for mapping in proposal.mappings:
        reason = None
        if mapping.target not in ALLOWED_TARGETS:
            reason = f"unknown target '{mapping.target}' (not in the universal schema)"
        elif mapping.raw_field not in known_fields:
            reason = f"raw field '{mapping.raw_field}' does not occur in the samples"
        elif mapping.target in used_targets:
            reason = f"target '{mapping.target}' is already mapped by another field"
        elif mapping.raw_field in used_fields:
            reason = f"raw field '{mapping.raw_field}' is already mapped"
        elif mapping.type is not None and mapping.target in SPECIAL_TARGETS:
            reason = f"type coercion is not supported for special target '{mapping.target}'"
        if reason:
            rejected.append({**mapping.model_dump(), "reason": reason})
            continue
        used_targets.add(mapping.target)
        used_fields.add(mapping.raw_field)
        accepted.append(mapping.model_dump())
    if not accepted:
        issues.append("No valid field mappings remain.")
    return {"issues": issues, "accepted": accepted, "rejected": rejected}


def suggested_adapter_id(proposal: AdapterProposal) -> str:
    """Stable identity derived from vendor + product, e.g. 'acme_gateway'."""
    base = re.sub(r"[^a-z0-9]+", "_", f"{proposal.vendor} {proposal.product}".lower()).strip("_")
    base = base or "onboarded_source"
    if not base[0].isalpha():
        base = f"src_{base}"
    return base[:64] if len(base) >= 3 else f"{base}_src"


def to_adapter(
    proposal: AdapterProposal,
    accepted_mappings: list[dict[str, Any]],
    *,
    adapter_id: str,
    version: int,
    description: str,
) -> AdapterMapping:
    """Build the runtime AdapterMapping (validated exactly like shipped YAML)."""
    class_name, category_uid, category_name = OCSF_CLASSES[proposal.ocsf_class_uid]
    field_map: dict[str, FieldMapEntry] = {}
    special: dict[str, str] = {}
    for mapping in accepted_mappings:
        target = mapping["target"]
        if target in SPECIAL_TARGETS:
            special[SPECIAL_TARGETS[target]] = mapping["raw_field"]
        else:
            field_map[mapping["raw_field"]] = FieldMapEntry(target=target, type=mapping.get("type"))
    return AdapterMapping(
        id=adapter_id,
        vendor=proposal.vendor,
        product=proposal.product,
        format=proposal.format,
        version=str(version),
        description=description,
        match=MatchRule(
            format=proposal.format,
            field=proposal.match.field,
            equals=proposal.match.equals,
            contains=proposal.match.contains,
        ),
        ocsf=OCSFClassification(
            class_uid=proposal.ocsf_class_uid,
            class_name=class_name,
            category_uid=category_uid,
            category_name=category_name,
        ),
        event_type=proposal.event_type,
        severity_map=proposal.severity_map,
        timestamp_format=proposal.timestamp_format,
        field_map=field_map,
        parser=proposal.parser.to_declarative(),
        source="onboarded",
        **special,
    )


# JSON schema handed to the LLM's structured-output mode. Intentionally
# simple (types, enums, required, no additional properties); the strict
# Pydantic model above remains the authority and re-validates everything.
_STR = {"type": "string"}
PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vendor", "product", "format", "parser", "match", "ocsf_class_uid", "mappings",
                 "overall_confidence", "reasoning", "assumptions", "ambiguous_fields"],
    "properties": {
        "vendor": _STR,
        "product": _STR,
        "format": {"type": "string", "enum": ["syslog", "json", "cef", "delimited", "kv"]},
        "parser": {
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy"],
            "properties": {
                "strategy": {"type": "string", "enum": ["native", "delimited", "kv"]},
                "delimiter": {"type": "string", "enum": ["|", ",", ";", "\t"]},
                "columns": {"type": "array", "items": _STR},
                "pair_separator": {"type": "string", "enum": ["whitespace", ",", ";", "|"]},
                "kv_separator": {"type": "string", "enum": ["=", ":"]},
                "min_pairs": {"type": "integer"},
            },
        },
        "match": {
            "type": "object",
            "additionalProperties": False,
            "required": ["field"],
            "properties": {"field": _STR, "equals": _STR, "contains": _STR},
        },
        "ocsf_class_uid": {"type": "integer", "enum": sorted(OCSF_CLASSES)},
        "event_type": _STR,
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["raw_field", "target", "confidence", "evidence"],
                "properties": {
                    "raw_field": _STR,
                    "target": {"type": "string", "enum": sorted(ALLOWED_TARGETS)},
                    "type": {"type": "string", "enum": ["str", "int", "float", "bool"]},
                    "confidence": {"type": "number"},
                    "evidence": _STR,
                },
            },
        },
        "severity_map": {"type": "object", "additionalProperties": _STR},
        "timestamp_format": _STR,
        "overall_confidence": {"type": "number"},
        "reasoning": {"type": "array", "items": _STR},
        "assumptions": {"type": "array", "items": _STR},
        "ambiguous_fields": {"type": "array", "items": _STR},
    },
}
