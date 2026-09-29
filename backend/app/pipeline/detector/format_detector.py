"""Format detection: classifies a raw log line as syslog, json, cef, or unknown.

This runs before any parser is invoked, so it must be cheap and must never
raise — an undetectable format is a legitimate, expected outcome (UNKNOWN),
not an error.
"""
import json
import re

from app.schema.ocsf import FormatType

_SYSLOG_PRI_RE = re.compile(r"^<\d{1,3}>")
_CEF_MARKER_RE = re.compile(r"CEF:\d+\|")
_RFC3164_TIMESTAMP_RE = re.compile(r"^(?:<\d{1,3}>)?[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\S+")


def detect_format(raw_log: str) -> FormatType:
    text = raw_log.strip()
    if not text:
        return FormatType.UNKNOWN

    # CEF is checked before syslog because a CEF event is frequently wrapped
    # in a syslog header (e.g. "<134>Jan 18 2021 ... CEF:0|Vendor|...").
    if _CEF_MARKER_RE.search(text):
        return FormatType.CEF

    if _looks_like_json(text):
        return FormatType.JSON

    if _SYSLOG_PRI_RE.match(text) or _RFC3164_TIMESTAMP_RE.match(text):
        return FormatType.SYSLOG

    return FormatType.UNKNOWN


def _looks_like_json(text: str) -> bool:
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return False
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, (dict, list))


# --------------------------------------------------------------------------
# Phase 8: secondary native-format detection (LEEF / XML).
#
# detect_format() above is unchanged. The orchestrator calls this ONLY when
# detect_format() returned UNKNOWN *and* no human-approved onboarded adapter
# matched, so existing detection, syslog-wrapped payloads and onboarded
# sources keep priority. Never raises.
# --------------------------------------------------------------------------


def detect_secondary_format(raw_log: str) -> FormatType:
    from app.pipeline.parsers.leef_parser import header_parts
    from app.pipeline.parsers.xml_safety import is_safe_xml, looks_like_xml

    try:
        text = raw_log.strip()
        if not text:
            return FormatType.UNKNOWN
        if text.startswith("LEEF:"):
            return FormatType.LEEF if header_parts(text) is not None else FormatType.UNKNOWN
        if text.startswith("<") and looks_like_xml(text) and is_safe_xml(text):
            return FormatType.XML
    except Exception:  # noqa: BLE001 — detection must never raise
        return FormatType.UNKNOWN
    return FormatType.UNKNOWN
