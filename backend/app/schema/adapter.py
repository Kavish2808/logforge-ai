"""Pydantic validation model for YAML vendor mapping files.

Every adapter YAML file under app/adapters/mappings/ is loaded and
validated against `AdapterMapping` before it can be used by the pipeline.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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

    def resolved_field_map(self) -> dict[str, FieldMapEntry]:
        resolved: dict[str, FieldMapEntry] = {}
        for key, value in self.field_map.items():
            resolved[key] = value if isinstance(value, FieldMapEntry) else FieldMapEntry(target=value)
        return resolved
