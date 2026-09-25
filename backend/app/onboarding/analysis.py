"""Deterministic multi-sample analysis of an unknown log source.

Produces the *evidence* every later step relies on: which format the
samples are in, which fields exist in how many samples, what their values
look like (IP, port, timestamp, severity, ...), which values are constant
(identity candidates), and how consistent the structure is. Pure function:
no DB, no network, no LLM. Only fields actually observed in the samples
are ever reported — nothing is invented.
"""
from __future__ import annotations

import csv
import ipaddress
import os
import re
from collections import Counter
from typing import Any

from app.pipeline.detector.format_detector import detect_format
from app.pipeline.fingerprint.structural import compute_fingerprint
from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.declarative import split_kv_pairs
from app.pipeline.parsers.registry import get_parser

MAX_SAMPLES = 50
MAX_SAMPLE_CHARS = 16_000
MAX_EXAMPLES_PER_FIELD = 3
MAX_EXAMPLE_CHARS = 64
MIN_DECLARATIVE_FIELDS = 3
CANDIDATE_DELIMITERS = ("|", ",", ";", "\t")

# Constant, author-written patterns (never AI-generated); inputs are bounded.
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")
_SYSLOG_TS_RE = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}$")
_DATE_TIME_RE = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}$|^\d{2}:\d{2}:\d{2}$")
_EPOCH_RE = re.compile(r"^\d{10}(\d{3})?$")
_INT_RE = re.compile(r"^-?\d{1,18}$")
_FLOAT_RE = re.compile(r"^-?\d{1,18}\.\d{1,18}$")

SEVERITY_WORDS = {
    "emergency", "emerg", "alert", "critical", "crit", "error", "err", "warning", "warn",
    "notice", "info", "informational", "debug", "low", "medium", "high", "very-high", "fatal",
}
SEVERITY_NAME_HINTS = {"severity", "sev", "level", "priority", "pri", "risk", "loglevel", "log_level"}
IDENTITY_NAME_HINTS = (
    "vendor", "product", "device_vendor", "device_product", "app_name", "appname", "program",
    "source", "service", "devtype", "device_type", "log_source", "logsource", "sourcetype",
)


def analyze_samples(samples: list[str]) -> dict[str, Any]:
    """Evidence report for the samples (JSON-serializable)."""
    per_sample: list[dict[str, Any]] = []
    for index, raw in enumerate(samples):
        detected = detect_format(raw).value
        entry: dict[str, Any] = {"index": index, "detected_format": detected, "fields": None, "error": None}
        if detected != "unknown":
            parser = get_parser(detected)
            try:
                entry["fields"] = parser.parse(raw).fields if parser else None
            except ParserError as exc:
                entry["error"] = str(exc)[:200]
        per_sample.append(entry)

    notes: list[str] = []
    unknown = [e for e in per_sample if e["detected_format"] == "unknown"]
    declarative_hint: dict[str, Any] | None = None
    if unknown:
        declarative_hint = _infer_declarative([samples[e["index"]] for e in unknown])
        if declarative_hint:
            for e in unknown:
                fields = _parse_with_hint(samples[e["index"]], declarative_hint)
                if fields is not None:
                    e["fields"] = fields
                    e["detected_format"] = declarative_hint["strategy"]
                else:
                    e["error"] = f"does not fit the inferred {declarative_hint['strategy']} structure"
        else:
            notes.append("No consistent key-value or delimited structure found in the unrecognized samples.")

    format_counts = Counter(e["detected_format"] for e in per_sample)
    dominant_format = format_counts.most_common(1)[0][0] if per_sample else "unknown"
    parsed = [e for e in per_sample if e["detected_format"] == dominant_format and e["fields"] is not None]
    fields = _field_evidence([e["fields"] for e in parsed])

    signatures = Counter(compute_fingerprint(e["fields"])["signature"] for e in parsed)
    common = sorted(name for name, f in fields.items() if f["present_in"] == len(parsed) and parsed)
    optional = sorted(name for name, f in fields.items() if f["present_in"] < len(parsed))

    prefix = os.path.commonprefix(samples) if len(samples) > 1 else ""
    if len(prefix) < 3:
        prefix = ""

    if dominant_format in ("syslog", "json", "cef"):
        strategy: dict[str, Any] = {"strategy": "native", "format": dominant_format}
    elif declarative_hint and dominant_format == declarative_hint["strategy"]:
        strategy = declarative_hint
    else:
        strategy = {"strategy": "none", "format": "unknown"}

    return {
        "sample_count": len(samples),
        "format_counts": dict(format_counts),
        "dominant_format": dominant_format,
        "parsed_in_dominant_format": len(parsed),
        "parser_hint": strategy,
        "parse_failures": [
            {"index": e["index"], "format": e["detected_format"], "error": e["error"]}
            for e in per_sample
            if e["fields"] is None or e["detected_format"] != dominant_format
        ],
        "fields": fields,
        "common_fields": common,
        "optional_fields": optional,
        "structure_variants": len(signatures),
        "dominant_structure_share": round(signatures.most_common(1)[0][1] / len(parsed), 4) if parsed else 0.0,
        "common_prefix": prefix[:MAX_EXAMPLE_CHARS],
        "vendor_indicators": _vendor_indicators(fields, len(parsed)),
        "notes": notes,
    }


# --------------------------------------------------------------------------
# Declarative structure inference (for formats the detector does not know)
# --------------------------------------------------------------------------


def _infer_declarative(samples: list[str]) -> dict[str, Any] | None:
    """Best of: key=value pairs, or a consistent delimited column count.
    Ties prefer key=value (named fields are stronger evidence than positions)."""
    kv_ok = sum(1 for s in samples if _kv_fields(s) is not None)
    best: dict[str, Any] | None = None
    best_score = 0
    if kv_ok:
        best = {"strategy": "kv", "format": "kv", "pair_separator": "whitespace", "kv_separator": "=",
                "min_pairs": MIN_DECLARATIVE_FIELDS}
        best_score = kv_ok
    for delimiter in CANDIDATE_DELIMITERS:
        counts = Counter(n for n in (_column_count(s, delimiter) for s in samples) if n >= MIN_DECLARATIVE_FIELDS)
        if not counts:
            continue
        column_count, fit = counts.most_common(1)[0]
        if fit > best_score:
            best = {"strategy": "delimited", "format": "delimited", "delimiter": delimiter,
                    "column_count": column_count,
                    "columns": [f"col_{i + 1}" for i in range(column_count)]}
            best_score = fit
    return best


def _kv_fields(sample: str) -> dict[str, str] | None:
    try:
        pairs = split_kv_pairs(sample.strip(), "whitespace", "=")
    except ParserError:
        return None
    return pairs if len(pairs) >= MIN_DECLARATIVE_FIELDS else None


def _column_count(sample: str, delimiter: str) -> int:
    text = sample.strip()
    if "\n" in text:
        return 0
    try:
        return len(next(csv.reader([text], delimiter=delimiter, strict=True)))
    except (csv.Error, StopIteration):
        return 0


def _parse_with_hint(sample: str, hint: dict[str, Any]) -> dict[str, Any] | None:
    if hint["strategy"] == "kv":
        return _kv_fields(sample)
    text = sample.strip()
    try:
        values = next(csv.reader([text], delimiter=hint["delimiter"], strict=True))
    except (csv.Error, StopIteration):
        return None
    if len(values) != hint["column_count"]:
        return None
    return {name: value.strip() for name, value in zip(hint["columns"], values)}


# --------------------------------------------------------------------------
# Field evidence
# --------------------------------------------------------------------------


def classify_value(value: Any) -> str:
    """Deterministic value class used as mapping evidence."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    text = str(value).strip()
    if not text:
        return "empty"
    if len(text) <= 45:
        try:
            ip = ipaddress.ip_address(text)
            return "ipv4" if ip.version == 4 else "ipv6"
        except ValueError:
            pass
    if _ISO_TS_RE.match(text) or _SYSLOG_TS_RE.match(text) or _DATE_TIME_RE.match(text):
        return "timestamp"
    if _EPOCH_RE.match(text):
        return "epoch"
    if _INT_RE.match(text):
        return "integer"
    if _FLOAT_RE.match(text):
        return "number"
    if text.lower() in {"true", "false"}:
        return "boolean"
    if text.lower() in SEVERITY_WORDS:
        return "severity_word"
    return "string"


def _field_evidence(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    order: dict[str, list[int]] = {}
    values: dict[str, list[Any]] = {}
    for record in records:
        for position, (name, value) in enumerate(record.items()):
            order.setdefault(name, []).append(position)
            values.setdefault(name, []).append(value)

    evidence: dict[str, dict[str, Any]] = {}
    total = len(records)
    for name, vals in values.items():
        classes = Counter(classify_value(v) for v in vals)
        meaningful = {k: v for k, v in classes.items() if k not in ("null", "empty")}
        dominant = max(meaningful.items(), key=lambda kv: (kv[1], kv[0]))[0] if meaningful else "null"
        hashable = [repr(v) for v in vals]
        distinct = len(set(hashable))
        constant = vals[0] if distinct == 1 and total > 0 and isinstance(vals[0], (str, int, float, bool)) else None
        examples: list[str] = []
        for v in vals:
            text = str(v)[:MAX_EXAMPLE_CHARS]
            if text not in examples:
                examples.append(text)
            if len(examples) >= MAX_EXAMPLES_PER_FIELD:
                break
        ints = [int(str(v).strip()) for v in vals if classify_value(v) == "integer"]
        evidence[name] = {
            "present_in": len(vals),
            "presence_ratio": round(len(vals) / total, 4) if total else 0.0,
            "required": len(vals) == total,
            "value_classes": dict(classes),
            "dominant_class": dominant,
            "type_consistent": len(meaningful) <= 1,
            "distinct_values": distinct,
            "constant_value": None if constant is None else str(constant)[:128],
            "positions": sorted(set(order[name]))[:10],
            "integer_range": [min(ints), max(ints)] if ints else None,
            "examples": examples,
        }
    return evidence


def _vendor_indicators(fields: dict[str, dict[str, Any]], parsed: int) -> list[dict[str, Any]]:
    """Constant values that could identify the source, strongest first."""
    indicators: list[dict[str, Any]] = []
    for name, f in fields.items():
        value = f["constant_value"]
        if value is None or not f["required"] or len(value) < 3:
            continue
        # Severity/level values are constant in many samples but never identify
        # a source; only plain string values are identity candidates.
        if f["dominant_class"] != "string" or name.lower() in SEVERITY_NAME_HINTS:
            continue
        named = name.lower() in IDENTITY_NAME_HINTS
        indicators.append(
            {
                "field": name,
                "value": value,
                "present_in": f["present_in"],
                "reason": (
                    f"field '{name}' is an identity-style field with constant value '{value}' in {f['present_in']}/{parsed} samples"
                    if named
                    else f"field '{name}' has constant value '{value}' in {f['present_in']}/{parsed} samples"
                ),
                "strength": 2 if named else 1,
            }
        )
    order = {n: i for i, n in enumerate(IDENTITY_NAME_HINTS)}
    indicators.sort(key=lambda i: (-i["strength"], order.get(i["field"].lower(), 99), i["field"]))
    return indicators
