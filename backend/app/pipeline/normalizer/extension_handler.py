"""Preserves every parsed field the active adapter did not explicitly consume.

This is what guarantees the "0% unknown fields lost" requirement: nothing
a parser extracts is ever dropped, it either lands in a typed OCSF field
or in `extensions`.
"""
from typing import Any


def compute_extensions(parsed_fields: dict[str, Any], consumed_keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in parsed_fields.items() if key not in consumed_keys}
