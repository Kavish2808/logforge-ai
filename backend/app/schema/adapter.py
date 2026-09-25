"""Pydantic validation model for YAML vendor mapping files.

Every adapter YAML file under app/adapters/mappings/ is loaded and
validated against `AdapterMapping` before it can be used by the pipeline.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Raw field / column names a declarative parser may produce. Deliberately
# conservative: identifiers only, bounded length.
FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")
MAX_DECLARATIVE_COLUMNS = 256
DECLARATIVE_FORMATS = ("delimited", "kv")


class DeclarativeParserConfig(BaseModel):
    """Configuration-only parser for log formats the built-in detector does
    not know (onboarded sources). It is pure data: there is no regex, no
    expression language and no code — only an enumerated strategy and
    enumerated separators, interpreted by app.pipeline.parsers.declarative.

    - delimited: one record per line, split on `delimiter` (CSV quoting
      rules), exactly len(columns) values named by `columns`.
    - kv:        key<kv_separator>value pairs separated by `pair_separator`
      (double-quoted values allowed); at least `min_pairs` pairs.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["delimited", "kv"]
    delimiter: Literal["|", ",", ";", "\t"] | None = None
    columns: list[str] = Field(default_factory=list)
    pair_separator: Literal["whitespace", ",", ";", "|"] = "whitespace"
    kv_separator: Literal["=", ":"] = "="
    min_pairs: int = Field(default=3, ge=1, le=64)

    @field_validator("columns")
    @classmethod
    def _valid_columns(cls, columns: list[str]) -> list[str]:
        if len(columns) > MAX_DECLARATIVE_COLUMNS:
            raise ValueError(f"at most {MAX_DECLARATIVE_COLUMNS} columns are allowed")
        for name in columns:
            if not FIELD_NAME_RE.match(name):
                raise ValueError(f"invalid column name {name!r}")
        if len(set(columns)) != len(columns):
            raise ValueError("column names must be unique")
        return columns

    @model_validator(mode="after")
    def _strategy_consistency(self) -> "DeclarativeParserConfig":
        if self.strategy == "delimited":
            if self.delimiter is None:
                raise ValueError("delimited strategy requires a delimiter")
            if len(self.columns) < 2:
                raise ValueError("delimited strategy requires at least 2 named columns")
        elif self.columns:
            raise ValueError("kv strategy does not take columns")
        return self


class MatchRule(BaseModel):
    """Rule used to decide whether an adapter applies to a parsed event."""

    format: str
    field: str | None = None
    contains: str | None = None
    equals: str | None = None


class OCSFClassification(BaseModel):
    class_uid: int
    class_name: str
    category_uid: int
    category_name: str


class FieldMapEntry(BaseModel):
    target: str
    type: str | None = None  # "int" | "float" | "bool" | "str" (default)


class AdapterMapping(BaseModel):
    id: str
    vendor: str
    product: str
    format: str  # syslog | json | cef
    version: str = "1"
    description: str | None = None

    match: MatchRule | None = None

    ocsf: OCSFClassification

    event_type: str | None = None
    event_action_field: str | None = None

    severity_field: str | None = None
    severity_map: dict[str, str] = Field(default_factory=dict)

    product_version_field: str | None = None

    timestamp_field: str | None = None
    timestamp_format: str | None = None  # None -> auto-detect

    field_map: dict[str, str | FieldMapEntry] = Field(default_factory=dict)
    static_fields: dict[str, str] = Field(default_factory=dict)

    # Phase 5 drift detection: extra raw (parser-level) field names that are
    # critical for this vendor, on top of DRIFT_CRITICAL_FIELDS (which are
    # OCSF targets resolved through field_map). Optional; not used by
    # normalization.
    critical_fields: list[str] = Field(default_factory=list)

    # Onboarding: a declarative parser for a format the built-in detector
    # does not recognize (format must then be "delimited" or "kv"). Shipped
    # YAML adapters never set it.
    parser: DeclarativeParserConfig | None = None
    # Where this mapping came from: "manual" (shipped YAML) or "onboarded"
    # (human-approved onboarding result). Reported as
    # processing_metadata.adapter_source on every event it normalizes.
    source: str = "manual"

    @model_validator(mode="after")
    def _declarative_consistency(self) -> "AdapterMapping":
        if self.parser is not None and self.format != self.parser.strategy:
            raise ValueError(f"format '{self.format}' must equal parser strategy '{self.parser.strategy}'")
        if self.format in DECLARATIVE_FORMATS and self.parser is None:
            raise ValueError(f"format '{self.format}' requires a declarative parser configuration")
        return self

    def resolved_field_map(self) -> dict[str, FieldMapEntry]:
        resolved: dict[str, FieldMapEntry] = {}
        for key, value in self.field_map.items():
            resolved[key] = value if isinstance(value, FieldMapEntry) else FieldMapEntry(target=value)
        return resolved
