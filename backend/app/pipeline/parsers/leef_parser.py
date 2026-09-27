"""Native LEEF parser (IBM Log Event Extended Format), Phase 8.

    LEEF:1.0|Vendor|Product|Version|EventID|<attributes, TAB-delimited>
    LEEF:2.0|Vendor|Product|Version|EventID|<DelimiterSpec>|<attributes>

LEEF 2.0 DelimiterSpec is a single character (e.g. ``^``), a hex code
(``x09``, ``0x09``, ``X5E``) or empty (TAB). Header fields may escape ``|``
as ``\\|``. Attributes are ``key=value`` pairs split on the delimiter; a
backslash escapes the delimiter, ``=``, ``|`` and ``\\`` inside a value.

Deterministic and bounded. Nothing is silently dropped:
- a token without ``=`` or with an invalid key is kept in
  ``leef_malformed_attributes`` (and the event gets a warning -> PARTIAL);
- a repeated key keeps its first value under the key and later values under
  ``key__2``, ``key__3`` ... (with a warning);
- attributes beyond MAX_ATTRIBUTES are kept verbatim in ``leef_unparsed_tail``.
Syslog-wrapped LEEF is NOT handled here: it is classified as syslog by the
unchanged built-in detector, exactly as before Phase 8.
"""
from __future__ import annotations

import re

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError

LEEF_SIGNATURE_RE = re.compile(r"^LEEF:(1\.0|2\.0)\|")
_KEY_RE = re.compile(r"^[A-Za-z_][\w.\-]{0,127}$")
MAX_ATTRIBUTES = 1_024
HEADER_FIELDS = ("device_vendor", "device_product", "device_version", "event_id")


def split_unescaped(text: str, sep: str, maxsplit: int = -1) -> list[str]:
    """Split on `sep` characters not preceded by a backslash escape; escapes are kept."""
    parts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch == sep and (maxsplit < 0 or len(parts) < maxsplit):
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unescape(value: str, delimiter: str | None = None) -> str:
    out: list[str] = []
    i, n = 0, len(value)
    specials = {"\\", "=", "|"} | ({delimiter} if delimiter else set())
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n and value[i + 1] in specials:
            out.append(value[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_delimiter(spec: str) -> str | None:
    """LEEF 2.0 delimiter spec -> delimiter character, or None if invalid."""
    if spec == "":
        return "\t"
    if len(spec) == 1:
        return spec
    m = re.fullmatch(r"(?:0?[xX])([0-9A-Fa-f]{2,4})", spec)
    if m:
        code = int(m.group(1), 16)
        if 0 < code < 0x110000 and chr(code) not in ("=", "\\"):
            return chr(code)
    return None


def header_parts(text: str) -> tuple[str, list[str]] | None:
    """(version, header+attribute parts) for a LEEF line with a complete header, else None."""
    m = LEEF_SIGNATURE_RE.match(text)
    if not m:
        return None
    version = m.group(1)
    count = 4 if version == "1.0" else 5  # header fields after the version
    parts = split_unescaped(text[m.end():], "|", maxsplit=count)
    if len(parts) < count:
        return None
    return version, parts


class LEEFParser(BaseParser):
    format_name = "leef"

    def parse(self, raw_log: str) -> ParseResult:
        text = raw_log.strip()
        header = header_parts(text)
        if header is None:
            raise ParserError("Malformed LEEF: expected 'LEEF:1.0|Vendor|Product|Version|EventID|' "
                              "or 'LEEF:2.0|Vendor|Product|Version|EventID|Delimiter|'")
        version, parts = header
        warnings: list[str] = []
        fields: dict = {"leef_version": version}
        for name, value in zip(HEADER_FIELDS, parts[:4]):
            fields[name] = _unescape(value)
        delimiter = "\t"
        if version == "2.0":
            spec = parts[4]
            fields["leef_delimiter"] = spec
            parsed = parse_delimiter(spec)
            if parsed is None:
                warnings.append(f"Unrecognized LEEF 2.0 delimiter spec {spec[:16]!r}; TAB assumed.")
            else:
                delimiter = parsed
            attributes = parts[5] if len(parts) > 5 else ""
        else:
            attributes = parts[4] if len(parts) > 4 else ""

        tokens = [t for t in split_unescaped(attributes, delimiter) if t != ""]
        if len(tokens) > MAX_ATTRIBUTES:
            fields["leef_unparsed_tail"] = delimiter.join(tokens[MAX_ATTRIBUTES:])
            warnings.append(f"More than {MAX_ATTRIBUTES} attributes; the remainder is preserved in 'leef_unparsed_tail'.")
            tokens = tokens[:MAX_ATTRIBUTES]
        malformed: list[str] = []
        repeats: dict[str, int] = {}
        for token in tokens:
            kv = split_unescaped(token, "=", maxsplit=1)
            key = kv[0].strip()
            if len(kv) != 2 or not _KEY_RE.match(key):
                malformed.append(token)
                continue
            value = _unescape(kv[1], delimiter)
            if key in fields:
                repeats[key] = repeats.get(key, 1) + 1
                fields[f"{key}__{repeats[key]}"] = value
            else:
                fields[key] = value
        if repeats:
            warnings.append("Repeated LEEF attribute(s) " + ", ".join(sorted(repeats))
                            + " preserved as <key>__<n>.")
        if malformed:
            fields["leef_malformed_attributes"] = malformed
            warnings.append(f"{len(malformed)} malformed LEEF attribute(s) preserved in 'leef_malformed_attributes'.")
        return ParseResult(fields=fields, format_detected=self.format_name, warnings=warnings)
