"""Edge-case hardening tests shared across the three parsers: empty/whitespace
input, unicode, very large fields, duplicate keys, unexpected delimiters,
and embedded newlines. No parser should ever raise anything other than
ParserError, and it must never crash the process.
"""
import time

import pytest

from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.cef_parser import CEFParser
from app.pipeline.parsers.json_parser import JSONParser
from app.pipeline.parsers.syslog_parser import SyslogParser

json_parser = JSONParser()
syslog_parser = SyslogParser()
cef_parser = CEFParser()


@pytest.mark.parametrize("parser", [json_parser, syslog_parser, cef_parser])
def test_empty_input_raises_parser_error_not_crash(parser):
    with pytest.raises(ParserError):
        parser.parse("")


@pytest.mark.parametrize("parser", [json_parser, syslog_parser, cef_parser])
def test_whitespace_only_input_raises_parser_error_not_crash(parser):
    with pytest.raises(ParserError):
        parser.parse("   \n\t  ")


# ---- Unicode ----


def test_json_preserves_unicode_exactly():
    raw = '{"message": "login failed for user 用户名 — café 🔥"}'
    result = json_parser.parse(raw)
    assert result.fields["message"] == "login failed for user 用户名 — café 🔥"


def test_syslog_preserves_unicode_in_message():
    raw = "<134>Jan 18 12:00:00 host1 app[1]: user 用户名 logged in — café"
    result = syslog_parser.parse(raw)
    assert "用户名" in result.fields["message"]
    assert "café" in result.fields["message"]


def test_cef_preserves_unicode_in_name_field():
    raw = "CEF:0|Vendor|Product|1.0|1|blocked user 用户名|5|src=10.0.0.1"
    result = cef_parser.parse(raw)
    assert result.fields["name"] == "blocked user 用户名"


# ---- Very large fields ----


def test_json_handles_very_large_field_value():
    large_value = "A" * 200_000
    raw = f'{{"message": "{large_value}"}}'
    result = json_parser.parse(raw)
    assert len(result.fields["message"]) == 200_000


def test_syslog_handles_very_large_message():
    large_message = "x" * 200_000
    raw = f"<134>Jan 18 12:00:00 host1 app[1]: {large_message}"
    result = syslog_parser.parse(raw)
    assert len(result.fields["message"]) == 200_000


def test_syslog_large_non_kv_message_does_not_hang_on_kv_flattening():
    # Regression test: a long run of word characters with no '=' at all
    # used to trigger catastrophic regex backtracking in the key=value
    # auto-flattening step (O(n^2)+), taking minutes on a message this
    # size. Must now complete in well under a second.
    large_message = "x" * 200_000
    raw = f"<134>Jan 18 12:00:00 host1 app[1]: {large_message}"
    start = time.monotonic()
    result = syslog_parser.parse(raw)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"KV flattening took {elapsed:.2f}s — likely catastrophic backtracking"
    assert len(result.fields["message"]) == 200_000


def test_cef_large_non_kv_extension_does_not_hang():
    large_extension = "x" * 200_000
    raw = f"CEF:0|Vendor|Product|1.0|1|Name|5|{large_extension}"
    start = time.monotonic()
    cef_parser.parse(raw)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"CEF extension parsing took {elapsed:.2f}s — likely catastrophic backtracking"


# ---- Duplicate / conflicting keys ----


def test_json_duplicate_keys_last_value_wins():
    # Standard JSON/stdlib behavior — documented here so it's a deliberate,
    # tested contract rather than an implicit accident.
    raw = '{"severity": "low", "severity": "high"}'
    result = json_parser.parse(raw)
    assert result.fields["severity"] == "high"


def test_syslog_duplicate_kv_keys_in_message_last_value_wins():
    raw = '<189>Jan 18 12:00:00 FGT100E FORTIGATE: a=1 b=2 c=3 action="accept" action="deny"'
    result = syslog_parser.parse(raw)
    assert result.fields["action"] == "deny"


# ---- Unexpected delimiters ----


def test_cef_unescaped_pipe_inside_extension_is_not_treated_as_a_delimiter():
    # Only the first 7 pipes delimit the header; everything after that is
    # the extension verbatim, even if it contains further '|' characters.
    raw = "CEF:0|Vendor|Product|1.0|1|Name|5|msg=a|b|c src=10.0.0.1"
    result = cef_parser.parse(raw)
    assert result.fields["msg"] == "a|b|c"
    assert result.fields["src"] == "10.0.0.1"


def test_cef_empty_extension_still_parses():
    raw = "CEF:0|Vendor|Product|1.0|1|Name|5|"
    result = cef_parser.parse(raw)
    assert result.fields["name"] == "Name"
    assert "src" not in result.fields


# ---- Embedded newlines (must not silently truncate or hard-fail) ----


def test_syslog_message_with_embedded_newline_is_fully_captured():
    raw = "<134>Jan 18 12:00:00 host1 app[1]: line one\nline two"
    result = syslog_parser.parse(raw)
    assert "line one" in result.fields["message"]
    assert "line two" in result.fields["message"]


# ---- Missing / extra fields ----


def test_json_missing_optional_fields_does_not_error():
    result = json_parser.parse('{"only_field": "value"}')
    assert result.fields == {"only_field": "value"}


def test_json_extra_unexpected_fields_are_all_preserved():
    raw = '{"message": "hi", "totally_unexpected_field_xyz": 123, "another_one": true}'
    result = json_parser.parse(raw)
    assert result.fields["totally_unexpected_field_xyz"] == 123
    assert result.fields["another_one"] is True


# ---- Partial / truncated input ----


def test_json_partial_truncated_object_raises_parser_error():
    with pytest.raises(ParserError):
        json_parser.parse('{"message": "cut off mid-str')


def test_syslog_partial_header_raises_parser_error():
    with pytest.raises(ParserError):
        syslog_parser.parse("<134>Jan 18 12:00")


def test_cef_partial_header_raises_parser_error():
    with pytest.raises(ParserError):
        cef_parser.parse("CEF:0|Vendor|Product")
