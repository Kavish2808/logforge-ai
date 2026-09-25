"""Structural fingerprint of a parsed event: the set and order of the
top-level fields the parser extracted from the raw log (before
normalization). This is stored on every event and is the basis for
drift detection (field-count/field-order comparison against a baseline)
in a later phase — here it is only computed and persisted.
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
    }
