"""Typed OCSF group fields (network/user/process) only ever receive values
of their declared type; anything else stays in extensions with a warning."""
from app.pipeline.normalizer.ocsf_mapper import normalize
from app.schema.adapter import AdapterMapping
from app.schema.ocsf import UniversalEvent


def adapter(field_map):
    return AdapterMapping.model_validate({
        "id": "t", "vendor": "V", "product": "P", "format": "json",
        "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
        "field_map": field_map,
    })


PORTS = {"sport": {"target": "network.src_port", "type": "int"}, "dport": {"target": "network.dst_port", "type": "int"},
         "src": {"target": "network.src_ip"}, "usr": {"target": "user.name"}, "pid": {"target": "process.pid", "type": "int"}}


def test_valid_typed_values_are_unchanged():
    r = normalize({"sport": "51422", "dport": 53, "src": "10.0.0.1", "usr": "jdoe", "pid": "42"}, adapter(PORTS))
    assert r.network == {"src_port": 51422, "dst_port": 53, "src_ip": "10.0.0.1"}
    assert r.user == {"name": "jdoe"} and r.process == {"pid": 42}
    assert r.extensions == {} and r.warnings == []


def test_dash_port_goes_to_extensions_with_warning():
    r = normalize({"sport": "51422", "dport": "-"}, adapter(PORTS))
    assert r.network == {"src_port": 51422}
    assert r.extensions == {"dport": "-"}
    assert len(r.warnings) == 1 and "'-' is not a valid network.dst_port" in r.warnings[0]


def test_wrong_json_types_go_to_extensions():
    r = normalize({"usr": 12345, "src": 167772161, "dport": {"p": 1}, "pid": [1]}, adapter(PORTS))
    assert r.user == {} and r.network == {} and r.process == {}
    assert r.extensions == {"usr": 12345, "src": 167772161, "dport": {"p": 1}, "pid": [1]}
    assert len(r.warnings) == 4


def test_none_values_remain_valid():
    r = normalize({"pid": None, "usr": None}, adapter(PORTS))
    assert r.process == {"pid": None} and r.user == {"name": None} and r.warnings == []


def test_rejected_value_frees_the_target_for_a_later_field():
    fm = {"dport": {"target": "network.dst_port", "type": "int"}, "dpt2": {"target": "network.dst_port", "type": "int"}}
    r = normalize({"dport": "-", "dpt2": "443"}, adapter(fm))
    assert r.network == {"dst_port": 443} and r.extensions == {"dport": "-"}


def test_flat_targets_keep_existing_coercion_behavior():
    r = normalize({"b": "not-a-number"}, adapter({"b": {"target": "bytes_sent", "type": "int"}}))
    assert r.normalized_event["bytes_sent"] == "not-a-number"
    assert any("Could not coerce" in w for w in r.warnings)


def test_normalized_groups_always_fit_the_read_model():
    r = normalize({"sport": "x", "dport": "-", "src": 1, "usr": 2, "pid": "p"}, adapter(PORTS))
    UniversalEvent.model_validate({
        "event_id": "E", "raw_event": "r", "raw_hash": "h", "received_at": "2026-01-01T00:00:00Z",
        "format_detected": "json", "status": "PARTIAL",
        "network": r.network, "user": r.user, "process": r.process, "extensions": r.extensions,
    })
