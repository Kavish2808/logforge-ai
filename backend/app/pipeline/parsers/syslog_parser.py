"""Parser for syslog messages, supporting both RFC 5424 and legacy RFC 3164."""
import re

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError

_re_flags = re.DOTALL  # let the trailing "message" group span embedded newlines

_RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<version>\d+)\s+"
    r"(?P<timestamp>\S+)\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<appname>\S+)\s+"
    r"(?P<procid>\S+)\s+"
    r"(?P<msgid>\S+)\s+"
    r"(?P<structured_data>-|\[.*\])\s?"
    r"(?P<message>.*)$",
    _re_flags,
)

_RFC3164_RE = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<tag>[^\s:\[]+)(?:\[(?P<pid>\d+)\])?:\s*"
    r"(?P<message>.*)$",
    _re_flags,
)

# Many appliances (Fortinet, etc.) emit a syslog "message" body that is
# itself a sequence of key=value pairs. This is a generic capability of
# the syslog parser (not vendor-specific): when the message looks like
# key=value data, flatten it into top-level fields so adapter YAML
# mappings can reference those keys directly, without any Python code
# per vendor.
# The key length is bounded (no real field name is anywhere near this long)
# specifically to prevent catastrophic backtracking: an unbounded `[\w-]*`
# combined with `finditer` scanning every position of a long run of word
# characters with no '=' (e.g. a huge non-KV message body) is O(n^2)+ and
# can hang for seconds to minutes on realistic input sizes.
_KV_RE = re.compile(r'(?P<key>[A-Za-z_][\w-]{0,127})=(?P<value>"(?:[^"\\]|\\.)*"|\S+)')
_MIN_KV_PAIRS_TO_FLATTEN = 3


def _extract_kv_pairs(message: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in _KV_RE.finditer(message):
        value = m.group("value")
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        pairs[m.group("key")] = value
    return pairs


class SyslogParser(BaseParser):
    format_name = "syslog"

    def parse(self, raw_log: str) -> ParseResult:
        text = raw_log.strip()
        warnings: list[str] = []

        match = _RFC5424_RE.match(text)
        if match:
            data = match.groupdict()
            pri = int(data["pri"])
            fields = {
                "facility": pri // 8,
                "severity": pri % 8,
                "syslog_version": data["version"],
                "timestamp": data["timestamp"],
                "hostname": data["hostname"],
                "app_name": data["appname"],
                "proc_id": None if data["procid"] == "-" else data["procid"],
                "msg_id": None if data["msgid"] == "-" else data["msgid"],
                "structured_data": None if data["structured_data"] == "-" else data["structured_data"],
                "message": data["message"],
                "rfc": "5424",
            }
            self._flatten_kv_payload(fields, data["message"])
            return ParseResult(fields=fields, format_detected=self.format_name, warnings=warnings)

        match = _RFC3164_RE.match(text)
        if match:
            data = match.groupdict()
            fields: dict = {
                "timestamp": data["timestamp"],
                "hostname": data["hostname"],
                "app_name": data["tag"],
                "proc_id": data.get("pid"),
                "message": data["message"],
                "rfc": "3164",
            }
            if data["pri"] is not None:
                pri = int(data["pri"])
                fields["facility"] = pri // 8
                fields["severity"] = pri % 8
            else:
                warnings.append("No PRI header found; facility/severity unavailable")
            self._flatten_kv_payload(fields, data["message"])
            return ParseResult(fields=fields, format_detected=self.format_name, warnings=warnings)

        raise ParserError(f"Unable to parse as syslog (RFC3164/RFC5424): {text[:120]!r}")

    @staticmethod
    def _flatten_kv_payload(fields: dict, message: str) -> None:
        kv_pairs = _extract_kv_pairs(message)
        if len(kv_pairs) < _MIN_KV_PAIRS_TO_FLATTEN:
            return
        for key, value in kv_pairs.items():
            fields.setdefault(key, value)
