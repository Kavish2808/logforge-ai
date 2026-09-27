"""Canonical JSON: the one serialization every Phase 7 hash is computed over.

Keys sorted, no insignificant whitespace, UTF-8, datetimes as ISO 8601 UTC.
Two semantically equal documents always produce identical bytes."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not canonically serializable: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
