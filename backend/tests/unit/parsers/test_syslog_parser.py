import pytest

from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.syslog_parser import SyslogParser
from tests.conftest import load_fixture

parser = SyslogParser()


def test_parses_rfc3164():
    result = parser.parse(load_fixture("syslog_samples/rfc3164_valid.txt"))
    assert result.format_detected == "syslog"
    assert result.fields["rfc"] == "3164"
    assert result.fields["hostname"] == "webserver01"
    assert result.fields["app_name"] == "sshd"
    assert result.fields["proc_id"] == "1234"
    assert "Failed password" in result.fields["message"]
    assert result.fields["facility"] == 16
    assert result.fields["severity"] == 6


def test_parses_rfc5424():
    result = parser.parse(load_fixture("syslog_samples/rfc5424_valid.txt"))
    assert result.fields["rfc"] == "5424"
    assert result.fields["hostname"] == "webserver01"
    assert result.fields["app_name"] == "myapp"
    assert result.fields["proc_id"] == "1234"
    assert result.fields["msg_id"] == "ID47"
    assert result.fields["message"] == "Application started successfully"


def test_rfc3164_without_pri_header_still_parses_with_warning():
    text = "Jan 18 12:00:00 host1 myapp[99]: something happened"
    result = parser.parse(text)
    assert result.fields["hostname"] == "host1"
    assert "facility" not in result.fields
    assert any("PRI" in w for w in result.warnings)


def test_fortinet_message_is_flattened_into_kv_fields():
    result = parser.parse(load_fixture("syslog_samples/fortinet.txt"))
    assert result.fields["app_name"] == "FORTIGATE"
    assert result.fields["srcip"] == "10.0.0.15"
    assert result.fields["dstip"] == "8.8.8.8"
    assert result.fields["dstport"] == "53"
    assert result.fields["action"] == "accept"
    assert result.fields["devname"] == "FGT100E"
    # original raw message is still preserved in full
    assert "srcip=10.0.0.15" in result.fields["message"]


def test_cisco_asa_parses_as_syslog_with_asa_tag():
    result = parser.parse(load_fixture("syslog_samples/cisco_asa.txt"))
    assert result.fields["app_name"] == "%ASA-6-302013"
    assert "Built outbound TCP connection" in result.fields["message"]


def test_malformed_syslog_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse(load_fixture("syslog_samples/malformed.txt"))


def test_malformed_syslog_with_pri_prefix_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse(load_fixture("syslog_samples/malformed_pri_prefix.txt"))
