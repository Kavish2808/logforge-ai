"""Parser for ArcSight Common Event Format (CEF).

Header: CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension
The header fields are pipe-delimited (escaped pipes allowed); the
extension is a space-separated set of key=value pairs (escaped '=' and
spaces-in-values allowed).
"""
import re

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError

_CEF_MARKER_RE = re.compile(r"CEF:\d+\|")

# Key length bounded to 128 chars (real CEF extension keys are short) to
# avoid the same class of catastrophic-backtracking blowup described in
# syslog_parser._KV_RE — an unbounded key class here is just as exploitable.
_EXTENSION_KV_RE = re.compile(
    r"(?P<key>[A-Za-z][\w.]{0,127})=(?P<value>(?:\\.|[^\\=])*?)(?=(?:\s+[A-Za-z][\w.]{0,127}=)|\s*$)",
    re.DOTALL,
)


def _escaped_split(text: str, maxsplit: int) -> list[str]:
    """Split on unescaped '|' characters, stopping after `maxsplit` splits."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == "|" and len(parts) < maxsplit:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unescape(value: str) -> str:
    return (
        value.replace("\\|", "|")
        .replace("\\=", "=")
        .replace("\\n", "\n")
        .replace("\\\\", "\\")
    )


class CEFParser(BaseParser):
    format_name = "cef"

    def parse(self, raw_log: str) -> ParseResult:
        text = raw_log.strip()
        warnings: list[str] = []

        marker = _CEF_MARKER_RE.search(text)
        if not marker:
            raise ParserError("No 'CEF:<version>|' marker found in log")

        prefix = text[: marker.start()].strip()
        body = text[marker.start() + len("CEF:") :]

        parts = _escaped_split(body, maxsplit=7)
        if len(parts) < 8:
            raise ParserError(
                f"Malformed CEF header: expected 8 pipe-delimited fields, got {len(parts)}"
            )

        version, vendor, product, product_version, signature_id, name, severity, extension = parts

        fields: dict = {
            "cef_version": _unescape(version),
            "device_vendor": _unescape(vendor),
            "device_product": _unescape(product),
            "device_version": _unescape(product_version),
            "signature_id": _unescape(signature_id),
            "name": _unescape(name),
            "severity": _unescape(severity),
        }

        for kv in _EXTENSION_KV_RE.finditer(extension):
            key = kv.group("key")
            value = _unescape(kv.group("value").strip())
            if value:
                fields[key] = value

        if prefix:
            # A syslog envelope wrapping the CEF payload (e.g. "<134>Jan 18
            # ... CEF:0|...") is the normal, expected shape for most
            # real-world CEF integrations (see mappings/vendors/paloalto.yaml)
            # — successfully handling it is not a degraded outcome, so this
            # is preserved as data, not raised as a warning.
            fields["syslog_prefix"] = prefix

        return ParseResult(fields=fields, format_detected=self.format_name, warnings=warnings)
