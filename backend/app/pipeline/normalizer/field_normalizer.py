"""Type coercion and timestamp normalization for mapped fields."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dateutil import parser as dateutil_parser


def coerce_type(value: Any, type_name: str | None) -> tuple[Any, str | None]:
    """Best-effort type coercion. Returns (value, warning). On failure, the
    original value is kept (a bad type hint in a mapping must never drop
    data) but a warning is returned so the failure is visible rather than
    silently corrupting/mistyping the normalized event."""
    if value is None or type_name is None:
        return value, None
    try:
        if type_name == "int":
            return int(str(value).strip()), None
        if type_name == "float":
            return float(str(value).strip()), None
        if type_name == "bool":
            if isinstance(value, bool):
                return value, None
            return str(value).strip().lower() in {"1", "true", "yes", "accept", "allow"}, None
        return str(value), None
    except (TypeError, ValueError):
        return value, f"Could not coerce value {value!r} to type '{type_name}'; kept as original value."


def parse_timestamp(value: str | None, fmt: str | None = None) -> tuple[datetime | None, str | None]:
    """Parse a timestamp string. Returns (parsed_datetime, warning_message).

    If `fmt` is given, uses strptime; otherwise falls back to flexible
    (dateutil) auto-detection, which covers RFC3164/RFC5424/ISO8601/etc.
    """
    if not value:
        return None, None
    try:
        if fmt:
            dt = datetime.strptime(value, fmt)
        else:
            dt = dateutil_parser.parse(value, default=datetime.now(tz=timezone.utc))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, None
    except (ValueError, OverflowError, TypeError) as exc:
        return None, f"Failed to parse timestamp '{value}': {exc}"
