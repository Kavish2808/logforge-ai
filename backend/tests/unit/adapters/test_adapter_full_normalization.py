"""For every shipped adapter (3 vendor + 3 generic fallback), verify the
complete normalized output against a realistic fixture — not just that an
adapter was selected, but that OCSF classification, typed nested groups,
severity mapping, and extensions are all correct end to end. Runs through
the real orchestrator (deterministic, no DB, no network).
"""
from app.pipeline.orchestrator import process
from tests.conftest import load_fixture


def test_cisco_asa_full_normalization():
    result = process(load_fixture("syslog_samples/cisco_asa.txt"))

    assert result.status == "SUCCESS"
    assert result.format_detected == "syslog"
    assert result.adapter_id == "cisco_asa"
    assert result.vendor == "Cisco"
    assert result.product == "ASA"
    assert result.ocsf_class_uid == 4001
    assert result.ocsf_class_name == "Network Activity"
    assert result.event_type == "Firewall"
    assert result.severity == "Informational"
    assert "Built outbound TCP connection" in result.event_action
    assert result.normalized_event["device_hostname"] == "ciscoasa"
    assert result.normalized_event["cisco_message_id"] == "%ASA-6-302013"
    assert result.structural_fingerprint["field_count"] > 0
    assert result.warnings == []


def test_fortinet_full_normalization():
    result = process(load_fixture("syslog_samples/fortinet.txt"))

    assert result.status == "SUCCESS"
    assert result.adapter_id == "fortinet"
    assert result.vendor == "Fortinet"
    assert result.product == "FortiGate"
    assert result.event_type == "Firewall"
    assert result.event_action == "accept"
    assert result.network == {
        "src_ip": "10.0.0.15",
        "src_port": 51422,
        "dst_ip": "8.8.8.8",
        "dst_port": 53,
        "protocol": "17",
    }
    assert result.normalized_event["device_hostname"] == "FGT100E"
    assert result.normalized_event["policy_id"] == "1"
    assert result.normalized_event["bytes_sent"] == 132
    assert result.normalized_event["bytes_received"] == 176
    assert result.normalized_event["event_message"] == "DNS query"
    # nothing from the flattened key=value payload is unaccounted for
    assert "devname" not in result.extensions  # consumed by the mapping
    assert result.warnings == []


def test_paloalto_full_normalization():
    result = process(load_fixture("cef_samples/paloalto.txt"))

    assert result.status == "SUCCESS"
    assert result.format_detected == "cef"
    assert result.adapter_id == "paloalto_cef"
    assert result.vendor == "Palo Alto Networks"
    assert result.product == "PAN-OS"
    assert result.product_version == "10.2.0"
    assert result.event_type == "Firewall"
    assert result.severity == "Medium"
    assert result.network == {
        "src_ip": "10.0.0.30",
        "src_port": 51500,
        "dst_ip": "93.184.216.34",
        "dst_port": 443,
        "protocol": "tcp",
    }
    assert result.user == {"name": "jdoe"}
    assert result.normalized_event["cs1_label"] == "URL"
    assert result.normalized_event["cs1_value"] == "example.com"
    assert result.warnings == []


def test_json_generic_full_normalization():
    result = process(load_fixture("json_samples/valid_login.json"))

    assert result.status == "SUCCESS"
    assert result.adapter_id == "json_generic"
    assert result.vendor == "Generic"
    assert result.ocsf_class_name == "Application Activity"
    assert result.network["src_ip"] == "10.0.0.5"
    assert result.user == {"name": "jdoe"}
    assert result.event_action == "User login successful"
    assert result.severity == "info"


def test_syslog_generic_full_normalization_for_unrecognized_vendor():
    raw = "<38>Jan 18 12:00:00 myhost sshd[555]: Accepted password for alice from 192.168.1.50 port 22 ssh2"
    result = process(raw)

    assert result.status == "SUCCESS"
    assert result.adapter_id == "syslog_generic"
    assert result.vendor == "Generic"
    assert result.ocsf_class_name == "System Activity"
    assert result.normalized_event["device_hostname"] == "myhost"
    assert result.normalized_event["process"] == {"name": "sshd", "pid": "555"}
    assert "Accepted password for alice" in result.event_action


def test_cef_generic_full_normalization_for_unrecognized_vendor():
    result = process(load_fixture("cef_samples/valid_generic.txt"))

    assert result.status == "SUCCESS"
    assert result.adapter_id == "cef_generic"
    assert result.vendor == "Generic"
    assert result.severity == "Very-High"
    assert result.network == {"src_ip": "10.0.0.1", "dst_ip": "2.1.2.2", "src_port": 1232}
    assert result.normalized_event["device_vendor"] == "Security"
    assert result.normalized_event["device_product"] == "threatmanager"


def test_cef_generic_suser_duser_do_not_collide():
    raw = "CEF:0|Security|threatmanager|1.0|100|access|5|suser=alice duser=bob src=10.0.0.1"
    result = process(raw)

    assert result.status == "SUCCESS"
    assert result.user == {"name": "alice"}
    assert result.normalized_event["destination_user"] == "bob"
    assert result.warnings == []
