"""Adaptive extension spill.

Splits an event's extensions into an inline part (kept in events.extensions)
and an overflow part (stored in event_extension_overflow) when the extension
payload exceeds the configured inline budget. Nothing is dropped, truncated
or rewritten: inline and overflow are disjoint and their union is exactly
the original mapping, with the original key order recorded for both.

Deterministic: the same extensions and budget always give the same split.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.evidence.canonical import canonical_bytes, canonical_sha256

INLINE = "INLINE"
SPILLED = "SPILLED"


@dataclass
class SpillResult:
    mode: str
    inline: dict[str, Any]
    overflow: dict[str, Any] = field(default_factory=dict)
    overflow_key_order: list[str] = field(default_factory=list)
    total_bytes: int = 0
    inline_bytes: int = 0
    overflow_bytes: int = 0
    overflow_sha256: str | None = None
    key_signature: str | None = None
    budget_bytes: int = 0
    budget_fields: int = 0

    def metadata(self) -> dict[str, Any]:
        """The record stored at processing_metadata.extension_spill."""
        return {
            "mode": self.mode,
            "inline_field_count": len(self.inline),
            "overflow_field_count": len(self.overflow),
            "total_bytes": self.total_bytes,
            "inline_bytes": self.inline_bytes,
            "overflow_bytes": self.overflow_bytes,
            "overflow_sha256": self.overflow_sha256,
            "key_signature": self.key_signature,
            "budget": {"max_bytes": self.budget_bytes, "max_fields": self.budget_fields},
            "location": "event_extension_overflow" if self.mode == SPILLED else None,
        }


def _entry_size(key: str, value: Any) -> int:
    # Size contribution of one key/value inside a canonical JSON object:
    # "key":value plus a separating comma.
    return len(canonical_bytes({key: value})) - 2 + 1


def key_signature(keys: list[str]) -> str:
    """Structure identity of an overflow: the sorted key set (values ignored)."""
    return canonical_sha256(sorted(keys))


def split_extensions(extensions: dict[str, Any], *, max_bytes: int, max_fields: int) -> SpillResult:
    total_bytes = len(canonical_bytes(extensions))
    if total_bytes <= max_bytes and len(extensions) <= max_fields:
        return SpillResult(mode=INLINE, inline=dict(extensions), total_bytes=total_bytes, inline_bytes=total_bytes,
                           budget_bytes=max_bytes, budget_fields=max_fields)

    inline: dict[str, Any] = {}
    overflow: dict[str, Any] = {}
    used = 2  # "{}"
    for key, value in extensions.items():  # original (parser) order
        size = _entry_size(key, value)
        if not overflow and len(inline) < max_fields and used + size <= max_bytes:
            inline[key] = value
            used += size
        else:
            # Once one field overflows, everything after it overflows too, so
            # the inline part is always a prefix of the original order.
            overflow[key] = value
    order = list(overflow)
    return SpillResult(
        mode=SPILLED,
        inline=inline,
        overflow=overflow,
        overflow_key_order=order,
        total_bytes=total_bytes,
        inline_bytes=len(canonical_bytes(inline)),
        overflow_bytes=len(canonical_bytes(overflow)),
        overflow_sha256=canonical_sha256(overflow),
        key_signature=key_signature(order),
        budget_bytes=max_bytes,
        budget_fields=max_fields,
    )


def merge(inline: dict[str, Any], overflow: dict[str, Any] | None, overflow_order: list[str] | None = None) -> dict[str, Any]:
    """Reassemble the full extension set (inline first, then overflow in its
    recorded order). Raises if the parts overlap — that would mean data was
    duplicated or overwritten somewhere."""
    if not overflow:
        return dict(inline)
    overlap = set(inline) & set(overflow)
    if overlap:
        raise ValueError(f"inline and overflow extensions overlap: {sorted(overlap)[:5]}")
    merged = dict(inline)
    for key in overflow_order or list(overflow):
        merged[key] = overflow[key]
    for key, value in overflow.items():  # defensive: keys missing from the recorded order
        merged.setdefault(key, value)
    return merged
