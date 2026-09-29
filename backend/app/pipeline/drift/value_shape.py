"""Critical-field value-shape drift (additive to Phase 5 structural drift).

Structural drift compares parser-level shape only: which fields exist, their
order, count and the JSON type of each value. For text formats (CEF, syslog
key=value, onboarded kv/delimited) every value is a string, so a critical
field whose *value* stops fitting its typed OCSF target - `spt=not-a-port`,
`src=10.0.0.999` - keeps the same structure and used to stay NORMAL.

This module checks the value of each critical field against the shape its
OCSF target defines, deterministically and without learning anything:

    network.src_port / network.dst_port   integer 0..65535   (NetworkInfo: int)
    network.src_ip   / network.dst_ip     IPv4 / IPv6 address (OCSF ip_t)
    timestamp                             parseable by the adapter's timestamp rules

Only these targets have a shape; `event_action`, `severity` and every other
field are free text, so ordinary value changes (a different action, user or
port number) are never findings. Empty values and vendor placeholders ("-",
"n/a", ...) mean "no value" and are not findings either. A value that does
not fit is reported with its expected and observed shape. The shapes observed
on the event that auto-bootstraps a source's baseline are accepted with that
baseline (the provisional reference is whatever was first observed), and a
human who accepts a drift records its observed shapes as accepted for the
source; an accepted shape is NORMAL from then on (see drift_service).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from app.pipeline.normalizer.field_normalizer import parse_timestamp

FIELD_VALUE_SHAPE_CHANGE = "FIELD_VALUE_SHAPE_CHANGE"
REASON_CRITICAL_VALUE_SHAPE_CHANGED = "CRITICAL_FIELD_VALUE_SHAPE_CHANGED"
CHANGE_KIND = "type_changed"  # the existing critical-change vocabulary (removed | type_changed)

PORT, IP, TIMESTAMP = "port", "ip", "timestamp"
SHAPE_BY_TARGET: dict[str, str] = {
    "network.src_port": PORT,
    "network.dst_port": PORT,
    "network.src_ip": IP,
    "network.dst_ip": IP,
    "timestamp": TIMESTAMP,
}
MAX_PORT = 65_535
# Vendor placeholders for "no value / not applicable" (e.g. FortiGate ICMP `dstport=-`). A placeholder is
# treated like an absent value - the event stays PARTIAL with the value preserved in extensions, as before -
# never as a shape change.
PLACEHOLDERS = frozenset({"-", "--", "n/a", "na", "none", "null", "unknown", "?"})
_NO_VALUE = frozenset({"null", "empty", "placeholder"})
_INT_RE = re.compile(r"^[+-]?\d+$")
_NUMBER_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$")
_PREVIEW = 64


def observed_shape(value: Any) -> str:
    """Deterministic label of what a value looks like (for explanations)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer" if 0 <= value <= MAX_PORT else "integer_out_of_port_range"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (dict, list)):
        return "object" if isinstance(value, dict) else "array"
    text = str(value).strip()
    if not text:
        return "empty"
    if text.lower() in PLACEHOLDERS:
        return "placeholder"
    if _INT_RE.match(text):
        return "integer" if 0 <= int(text) <= MAX_PORT else "integer_out_of_port_range"
    if _NUMBER_RE.match(text):
        return "number"
    if len(text) <= 45:
        try:
            ipaddress.ip_address(text)
            return "ip"
        except ValueError:
            pass
    return "string"


def fits(shape: str, value: Any, timestamp_format: str | None = None) -> bool:
    if shape == PORT:
        return observed_shape(value) == "integer"
    if shape == IP:
        return not isinstance(value, bool) and observed_shape(value) == "ip"
    if shape == TIMESTAMP:
        parsed, warning = parse_timestamp(value, timestamp_format)
        return parsed is not None and not warning
    return True


def check(
    critical_fields: dict[str, str | None],
    values: dict[str, Any],
    *,
    timestamp_format: str | None = None,
    accepted: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Critical fields (raw name -> OCSF target) whose present value does not fit the target's shape
    and whose observed shape was not previously accepted for the source. `values` holds the value of
    each critical raw field that is present (absent fields are structural removals, not checked here)."""
    findings: list[dict[str, Any]] = []
    accepted = accepted or {}
    for raw in sorted(critical_fields):
        target = critical_fields[raw]
        shape = SHAPE_BY_TARGET.get(target or "")
        if shape is None or raw not in values or values[raw] is None:
            continue
        value = values[raw]
        if observed_shape(value) in _NO_VALUE or fits(shape, value, timestamp_format):
            continue
        seen = "unparseable_timestamp" if shape == TIMESTAMP else observed_shape(value)
        if seen in accepted.get(raw, []):
            continue
        findings.append({
            "field": raw,
            "target": target,
            "expected_shape": shape,
            "observed_shape": seen,
            "value_preview": str(value)[:_PREVIEW],
            "reason": f"'{raw}' ({target}) must be {_describe(shape)}; got {seen} {str(value)[:_PREVIEW]!r}",
        })
    return findings


def as_critical_changes(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same findings in the Phase 5 critical-field-change shape (severity, explanation, UI):
    baseline_type = expected shape, current_type = observed shape. Details stay in the findings."""
    return [{"field": f["field"], "target": f["target"], "change": CHANGE_KIND,
             "baseline_type": f["expected_shape"], "current_type": f["observed_shape"]}
            for f in findings]


def _describe(shape: str) -> str:
    return {PORT: f"an integer port 0..{MAX_PORT}", IP: "an IPv4/IPv6 address",
            TIMESTAMP: "a timestamp the adapter can parse"}[shape]
