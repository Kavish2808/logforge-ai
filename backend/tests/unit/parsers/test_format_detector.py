import pytest

from app.pipeline.detector.format_detector import detect_format
from app.schema.ocsf import FormatType
from tests.conftest import load_fixture


@pytest.mark.parametrize(
    "fixture_path",
    [
        "syslog_samples/rfc3164_valid.txt",
        "syslog_samples/rfc5424_valid.txt",
        "syslog_samples/cisco_asa.txt",
        "syslog_samples/fortinet.txt",
    ],
)
def test_detects_syslog(fixture_path):
    assert detect_format(load_fixture(fixture_path)) == FormatType.SYSLOG


@pytest.mark.parametrize(
    "fixture_path",
    ["json_samples/valid_login.json", "json_samples/valid_unknown_fields.json"],
)
def test_detects_json(fixture_path):
    assert detect_format(load_fixture(fixture_path)) == FormatType.JSON


@pytest.mark.parametrize(
    "fixture_path",
    ["cef_samples/valid_generic.txt", "cef_samples/paloalto.txt"],
)
def test_detects_cef(fixture_path):
    assert detect_format(load_fixture(fixture_path)) == FormatType.CEF


def test_detects_unknown_for_plain_text():
    assert detect_format("just some random text with no structure") == FormatType.UNKNOWN


def test_detects_unknown_for_empty_string():
    assert detect_format("") == FormatType.UNKNOWN
    assert detect_format("   ") == FormatType.UNKNOWN


def test_malformed_json_falls_back_to_unknown():
    assert detect_format('{"not": "closed"') == FormatType.UNKNOWN


def test_cef_takes_priority_over_syslog_when_wrapped():
    wrapped = load_fixture("cef_samples/paloalto.txt")
    assert wrapped.startswith("<134>")
    assert detect_format(wrapped) == FormatType.CEF
