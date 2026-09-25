from app.pipeline.normalizer.ocsf_mapper import normalize
from app.schema.adapter import AdapterMapping


def _make_adapter(**overrides) -> AdapterMapping:
    base = dict(
        id="test_adapter",
        vendor="TestVendor",
        product="TestProduct",
        format="syslog",
        version="1",
        ocsf={
            "class_uid": 4001,
            "class_name": "Network Activity",
            "category_uid": 4,
            "category_name": "Network Activity",
        },
        event_action_field="message",
        severity_field="severity",
        severity_map={"0": "Emergency", "6": "Informational"},
        timestamp_field="timestamp",
        field_map={
            "src_ip": {"target": "network.src_ip"},
            "dst_port": {"target": "network.dst_port", "type": "int"},
            "user": {"target": "user.name"},
            "hostname": "device_hostname",
        },
    )
    base.update(overrides)
    return AdapterMapping.model_validate(base)


def test_maps_nested_network_and_user_fields():
    adapter = _make_adapter()
    parsed = {
        "src_ip": "10.0.0.1",
        "dst_port": "443",
        "user": "jdoe",
        "hostname": "host1",
        "message": "connection allowed",
        "severity": "6",
        "timestamp": "2026-01-18T12:00:00Z",
    }
    result = normalize(parsed, adapter)

    assert result.network == {"src_ip": "10.0.0.1", "dst_port": 443}
    assert result.user == {"name": "jdoe"}
    assert result.normalized_event["device_hostname"] == "host1"
    assert result.event_action == "connection allowed"
    assert result.severity == "Informational"
    assert result.ocsf_class_uid == 4001
    assert result.event_timestamp is not None
    assert result.event_timestamp.year == 2026


def test_unmapped_fields_go_to_extensions():
    adapter = _make_adapter()
    parsed = {
        "src_ip": "10.0.0.1",
        "message": "hi",
        "severity": "0",
        "totally_unknown_field": "should be preserved",
    }
    result = normalize(parsed, adapter)
    assert result.extensions == {"totally_unknown_field": "should be preserved"}


def test_severity_map_falls_back_to_raw_value_when_unmapped():
    adapter = _make_adapter()
    parsed = {"severity": "3"}  # not in severity_map
    result = normalize(parsed, adapter)
    assert result.severity == "3"


def test_missing_optional_fields_do_not_error():
    adapter = _make_adapter()
    result = normalize({}, adapter)
    assert result.network == {}
    assert result.user == {}
    assert result.event_action is None
    assert result.severity is None
    assert result.event_timestamp is None


def test_static_fields_are_always_added():
    adapter = _make_adapter(static_fields={"event_category": "network"})
    result = normalize({}, adapter)
    assert result.normalized_event["event_category"] == "network"


def test_bad_timestamp_produces_warning_not_exception():
    adapter = _make_adapter()
    result = normalize({"timestamp": "not-a-real-timestamp!!"}, adapter)
    assert result.event_timestamp is None
    assert any("timestamp" in w.lower() for w in result.warnings)


def test_conflicting_field_map_targets_do_not_silently_overwrite():
    # Two source fields mapping to the same target: the first (in YAML/dict
    # order) wins deterministically, and the second is preserved in
    # extensions rather than silently discarded, with a warning raised.
    adapter = _make_adapter(
        field_map={
            "src_ip": {"target": "network.src_ip"},
            "alt_src_ip": {"target": "network.src_ip"},
        }
    )
    result = normalize({"src_ip": "10.0.0.1", "alt_src_ip": "10.0.0.99"}, adapter)

    assert result.network == {"src_ip": "10.0.0.1"}
    assert result.extensions["alt_src_ip"] == "10.0.0.99"
    assert any("conflict" in w.lower() for w in result.warnings)


def test_failed_type_coercion_produces_warning_and_keeps_original_value():
    # The original value is kept, but in extensions: a value that does not fit
    # the typed OCSF field (network.dst_port: int) must never be placed there,
    # or the stored event cannot be read back (duplicate-event / 500 defect).
    adapter = _make_adapter(field_map={"dst_port": {"target": "network.dst_port", "type": "int"}})
    result = normalize({"dst_port": "not-a-port"}, adapter)

    assert "dst_port" not in result.network
    assert result.extensions["dst_port"] == "not-a-port"
    assert any("dst_port" in w and "preserved in extensions" in w for w in result.warnings)
