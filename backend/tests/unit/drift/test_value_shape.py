import pytest

from app.pipeline.drift import value_shape as vs

CRITICAL = {"spt": "network.src_port", "src": "network.src_ip", "act": "event_action", "ts": "timestamp", "sid": None}


@pytest.mark.parametrize("value,shape", [
    (0, "integer"), (65535, "integer"), ("443", "integer"), (" 22 ", "integer"), (65536, "integer_out_of_port_range"),
    ("-1", "integer_out_of_port_range"), ("1.5", "number"), ("10.0.0.1", "ip"), ("2001:db8::1", "ip"),
    ("10.0.0.999", "string"), ("not-a-port", "string"), ("", "empty"), ("-", "placeholder"), ("N/A", "placeholder"), (None, "null"), (True, "boolean"),
])
def test_observed_shape_is_deterministic(value, shape):
    assert vs.observed_shape(value) == shape


def test_only_typed_targets_are_checked_and_valid_values_pass():
    values = {"spt": "40000", "src": "192.0.2.1", "act": "anything at all", "ts": "2026-09-28T10:00:00Z", "sid": "x"}
    assert vs.check(CRITICAL, values) == []


def test_invalid_values_are_reported_with_expected_and_observed_shape():
    findings = vs.check(CRITICAL, {"spt": "port-x", "src": "host.example", "ts": "later", "act": "", "sid": "?"})
    assert [(f["field"], f["expected_shape"], f["observed_shape"]) for f in findings] == [
        ("spt", "port", "string"), ("src", "ip", "string"), ("ts", "timestamp", "unparseable_timestamp")]
    assert vs.as_critical_changes(findings)[0] == {"field": "spt", "target": "network.src_port", "change": "type_changed",
                                                   "baseline_type": "port", "current_type": "string"}


def test_absent_or_null_fields_are_left_to_structural_drift():
    assert vs.check(CRITICAL, {"spt": None}) == [] and vs.check(CRITICAL, {}) == []


def test_accepted_shapes_are_not_reported_but_other_shapes_are():
    accepted = {"spt": ["string"]}
    assert vs.check(CRITICAL, {"spt": "port-x"}, accepted=accepted) == []
    assert vs.check(CRITICAL, {"spt": "99999"}, accepted=accepted)[0]["observed_shape"] == "integer_out_of_port_range"


def test_value_preview_is_bounded():
    [f] = vs.check(CRITICAL, {"spt": "x" * 5000})
    assert len(f["value_preview"]) == 64


@pytest.mark.parametrize("marker", ["-", "--", "n/a", "NA", "none", "null", "Unknown", "?", "", "  "])
def test_placeholders_and_empty_values_are_no_value(marker):
    assert vs.check(CRITICAL, {"spt": marker, "src": marker, "ts": marker}) == []
