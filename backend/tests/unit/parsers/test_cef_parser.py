import pytest

from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.cef_parser import CEFParser
from tests.conftest import load_fixture

parser = CEFParser()


def test_parses_generic_cef():
    result = parser.parse(load_fixture("cef_samples/valid_generic.txt"))
    assert result.format_detected == "cef"
    assert result.fields["device_vendor"] == "Security"
    assert result.fields["device_product"] == "threatmanager"
    assert result.fields["device_version"] == "1.0"
    assert result.fields["signature_id"] == "100"
    assert result.fields["name"] == "worm successfully stopped"
    assert result.fields["severity"] == "10"
    assert result.fields["src"] == "10.0.0.1"
    assert result.fields["dst"] == "2.1.2.2"
    assert result.fields["spt"] == "1232"


def test_parses_cef_with_syslog_prefix():
    result = parser.parse(load_fixture("cef_samples/paloalto.txt"))
    assert result.fields["device_vendor"] == "Palo Alto Networks"
    assert result.fields["device_product"] == "PAN-OS"
    assert result.fields["device_version"] == "10.2.0"
    assert result.fields["src"] == "10.0.0.30"
    assert result.fields["dst"] == "93.184.216.34"
    assert result.fields["suser"] == "jdoe"
    assert result.fields["cs1Label"] == "URL"
    assert result.fields["cs1"] == "example.com"
    assert result.fields["syslog_prefix"] == "<134>Jan 18 12:10:00 PA-VM"
    # A syslog-wrapped CEF payload is the normal, expected shape — it must
    # not be treated as a degraded/partial parse.
    assert result.warnings == []


def test_escaped_pipe_in_extension_is_unescaped():
    raw = r"CEF:0|Vendor|Product|1.0|1|Name with \| pipe|5|msg=hello\=world"
    result = parser.parse(raw)
    assert result.fields["name"] == "Name with | pipe"
    assert result.fields["msg"] == "hello=world"


def test_malformed_cef_missing_fields_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse(load_fixture("cef_samples/malformed.txt"))


def test_no_cef_marker_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse("not a cef line at all")
