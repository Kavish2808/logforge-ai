"""Structural fingerprint of a parsed event: the set, order and value
types of the top-level fields the parser extracted from the raw log
(before normalization). This is stored on every event and is what drift
detection (app.pipeline.drift) compares against a per-source baseline.

`field_set`, `field_order`, `field_count` and `signature` are frozen since
Phase 0-4 and must stay byte-for-byte identical — `signature` in
particular is order-insensitive (sorted field names only). `field_types`
was added in Phase 5; it is additive and not part of the signature.
"""
import hashlib
from typing import Any


def compute_fingerprint(parsed_fields: dict[str, Any]) -> dict[str, Any]:
    field_order = list(parsed_fields.keys())
    field_set = sorted(field_order)
    signature = hashlib.sha256(",".join(field_set).encode("utf-8")).hexdigest()
    return {
        "field_set": field_set,
        "field_order": field_order,
        "field_count": len(field_order),
        "signature": signature,
        "field_types": compute_field_types(parsed_fields),
    }


def compute_field_types(parsed_fields: dict[str, Any]) -> dict[str, str]:
    """JSON-style type name of each top-level value. Nested structure is
    deliberately reduced to "object"/"array" — only the top level is
    fingerprinted."""
    return {key: _json_type_name(value) for key, value in parsed_fields.items()}


def _json_type_name(value: Any) -> str:
    # bool before int: bool is a subclass of int in Python.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "string"
