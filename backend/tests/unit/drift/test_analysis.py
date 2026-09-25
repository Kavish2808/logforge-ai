"""Unit tests for drift intelligence: classification, severity, critical
fields, recommendations and adapter-fallback evidence (pure, no DB)."""
import pytest

from app.pipeline.drift import analysis as a
from app.pipeline.drift.comparator import (
    REASON_CRITICAL_FIELD_CHANGED,
    REASON_FORMAT_CHANGED,
    REASON_SIMILARITY_BELOW_THRESHOLD,
    compare,
)
from app.pipeline.fingerprint.structural import compute_fingerprint
from app.schema.adapter import AdapterMapping

BASE = {f"f{i}": "v" for i in range(8)}
T = 0.85


def fp(fields):
    return compute_fingerprint(fields)


def adapter(**overrides) -> AdapterMapping:
    data = {
        "id": "acme",
        "vendor": "Acme",
        "product": "FW",
        "format": "syslog",
        "match": {"format": "syslog", "field": "app_name", "equals": "ACMEFW"},
        "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
        "event_action_field": "act",
        "severity_field": "sev",
        "field_map": {
            "sip": {"target": "network.src_ip"},
            "dip": {"target": "network.dst_ip"},
            "dport": {"target": "network.dst_port", "type": "int"},
            "usr": {"target": "user.name"},
        },
    }
    data.update(overrides)
    return AdapterMapping.model_validate(data)


DEFAULT_TARGETS = ["event_action", "severity", "network.src_ip", "network.dst_ip", "network.src_port", "network.dst_port"]


# --- A-F: classification ------------------------------------------------------------


def test_identical_structure_has_no_change_types():
    c = compare(fp(BASE), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == []


def test_added_field_classification():
    c = compare(fp({**BASE, "x": 1}), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [a.FIELD_ADDITION]


def test_removed_field_classification():
    c = compare(fp({k: v for k, v in BASE.items() if k != "f3"}), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [a.FIELD_REMOVAL]


def test_type_change_classification():
    c = compare(fp({**BASE, "f1": 5}), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [a.FIELD_TYPE_CHANGE]


def test_reorder_classification():
    c = compare(fp({k: "v" for k in reversed(list(BASE))}), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [a.FIELD_ORDER_CHANGE]


def test_added_field_alone_is_not_a_reorder():
    # Order is judged on the relative order of *shared* fields only.
    c = compare(fp({"x": "new", **BASE}), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [a.FIELD_ADDITION]


def test_multiple_simultaneous_changes():
    keys = [k for k in BASE if k != "f7"]
    keys[2], keys[3] = keys[3], keys[2]  # reorder two shared fields
    current = {"x": "new", **{k: "v" for k in keys}}
    current["f1"] = {"nested": 1}
    c = compare(fp(current), fp(BASE), threshold=T)
    assert a.classify_changes(c.differences) == [
        a.FIELD_ADDITION,
        a.FIELD_REMOVAL,
        a.FIELD_TYPE_CHANGE,
        a.FIELD_ORDER_CHANGE,
        a.MULTIPLE_STRUCTURAL_CHANGE,
    ]


def test_format_drift_classification():
    assert a.classify_changes({}, format_drift=True) == [a.FORMAT_DRIFT]
    assert a.classify_changes({"format_changed": {"baseline": "cef", "current": "json"}}) == [a.FORMAT_DRIFT]
    assert a.classify_changes({"added_fields": ["x"]}, format_drift=True) == [
        a.FIELD_ADDITION,
        a.FORMAT_DRIFT,
        a.MULTIPLE_STRUCTURAL_CHANGE,
    ]


# --- I: severity ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,level",
    [(0, "LOW"), (3, "LOW"), (4, "MEDIUM"), (8, "MEDIUM"), (9, "HIGH"), (14, "HIGH"), (15, "CRITICAL"), (40, "CRITICAL")],
)
def test_severity_band_boundaries(score, level):
    assert a.severity_for_score(score) == level


def _severity(current, base=BASE, critical=None):
    c = compare(fp(current), fp(base), threshold=T, critical_fields=critical)
    return a.compute_severity(c.differences, c.components, c.critical_changes)


def test_one_added_field_is_low():
    level, score, factors = _severity({**BASE, "x": "v"})
    assert (level, score) == ("LOW", 2)
    assert factors == {"added_fields": 1, "structural_magnitude": 1}


def test_two_added_fields_is_medium():
    level, score, _ = _severity({**BASE, "x": "v", "y": "v"})
    assert (level, score) == ("MEDIUM", 4)


def test_single_type_change_is_medium():
    level, score, factors = _severity({**BASE, "f1": 5})
    assert (level, score) == ("MEDIUM", 4)
    assert factors == {"type_changes": 4}


def test_reorder_only_is_low():
    level, score, _ = _severity({k: "v" for k in reversed(list(BASE))})
    assert (level, score) == ("LOW", 1)


def test_critical_field_removal_is_at_least_high():
    current = {k: v for k, v in BASE.items() if k != "f0"}
    level, score, factors = _severity(current, critical={"f0": "network.src_ip"})
    assert level == "HIGH"
    assert factors["critical_fields"] == a.SEVERITY_POINTS_CRITICAL_FIELD


def test_critical_floor_applies_even_with_low_points():
    # 6 points alone is MEDIUM; any critical-field change is floored at HIGH.
    level, score, _ = a.compute_severity(None, None, [{"field": "f0", "change": "removed"}])
    assert score == a.SEVERITY_POINTS_CRITICAL_FIELD
    assert a.severity_for_score(score) == "MEDIUM"
    assert level == "HIGH"


def test_two_critical_removals_are_critical():
    current = {k: v for k, v in BASE.items() if k not in ("f0", "f1")}
    level, _, _ = _severity(current, critical={"f0": "network.src_ip", "f1": "network.dst_ip"})
    assert level == "CRITICAL"


def test_format_change_is_always_critical():
    level, _, factors = a.compute_severity({}, {"field_set": 1.0}, [], format_changed=True)
    assert level == "CRITICAL"
    assert factors == {"format_change": a.SEVERITY_POINTS_FORMAT_CHANGE}


def test_adapter_fallback_same_format_is_high():
    level, score, _ = a.compute_severity(None, None, None, adapter_fallback=True)
    assert (level, score) == ("HIGH", 9)


def test_factors_sum_to_score_and_severity_is_not_similarity():
    # Two drifts with near-identical similarity but very different impact.
    removal = {k: v for k, v in BASE.items() if k != "f0"}
    plain = _severity(removal)
    critical = _severity(removal, critical={"f0": "network.src_ip"})
    assert plain[0] == "LOW" and critical[0] == "HIGH"
    for level, score, factors in (plain, critical):
        assert sum(factors.values()) == score


# --- G/H: critical fields ---------------------------------------------------------------


def test_resolve_critical_fields_through_adapter_yaml():
    resolved = a.resolve_critical_fields(adapter(), DEFAULT_TARGETS)
    assert resolved == {
        "act": "event_action",
        "sev": "severity",
        "sip": "network.src_ip",
        "dip": "network.dst_ip",
        "dport": "network.dst_port",
    }


def test_vendor_specific_critical_fields_from_yaml():
    resolved = a.resolve_critical_fields(adapter(critical_fields=["session_id"]), DEFAULT_TARGETS)
    assert resolved["session_id"] is None
    assert "usr" not in resolved  # user.name is not critical by default


def test_shipped_adapters_resolve_critical_fields():
    from app.adapters.loader import get_adapter_registry

    reg = get_adapter_registry()
    assert a.resolve_critical_fields(reg.get("paloalto_cef"), DEFAULT_TARGETS) == {
        "name": "event_action",
        "severity": "severity",
        "src": "network.src_ip",
        "dst": "network.dst_ip",
        "spt": "network.src_port",
        "dpt": "network.dst_port",
    }
    assert set(a.resolve_critical_fields(reg.get("fortinet"), DEFAULT_TARGETS)) == {
        "action", "level", "srcip", "dstip", "srcport", "dstport"
    }


def test_critical_field_removal_forces_drift_despite_high_similarity():
    current = {k: v for k, v in BASE.items() if k != "f0"}
    plain = compare(fp(current), fp(BASE), threshold=T)
    assert plain.is_drift is False  # ~0.906 >= 0.85
    c = compare(fp(current), fp(BASE), threshold=T, critical_fields={"f0": "network.src_ip"})
    assert c.is_drift is True
    assert c.decision_reasons == [REASON_CRITICAL_FIELD_CHANGED]
    assert c.critical_changes == [{"field": "f0", "target": "network.src_ip", "change": "removed"}]


def test_critical_field_type_change_forces_drift():
    c = compare(fp({**BASE, "f2": 443}), fp(BASE), threshold=T, critical_fields={"f2": "network.dst_port"})
    assert c.is_drift is True
    assert c.critical_changes == [
        {"field": "f2", "target": "network.dst_port", "change": "type_changed", "baseline_type": "string", "current_type": "integer"}
    ]


def test_critical_null_value_is_not_a_type_change():
    c = compare(fp({**BASE, "f2": None}), fp(BASE), threshold=T, critical_fields={"f2": "x"})
    assert c.is_drift is False and c.critical_changes == []


def test_critical_field_absent_from_baseline_is_ignored():
    c = compare(fp(BASE), fp(BASE), threshold=T, critical_fields={"not_in_baseline": "network.src_ip"})
    assert c.is_drift is False


def test_decision_reasons_combined():
    current = {"a": 1}
    c = compare(fp(current), fp(BASE), threshold=T, current_format="json", baseline_format="syslog",
                critical_fields={"f0": "network.src_ip"})
    assert c.decision_reasons == [REASON_FORMAT_CHANGED, REASON_SIMILARITY_BELOW_THRESHOLD, REASON_CRITICAL_FIELD_CHANGED]


# --- J: variants with critical rules -----------------------------------------------------


def test_variant_without_critical_change_is_preferred_over_higher_scoring_critical_one():
    current = {k: v for k, v in BASE.items() if k != "f0"}  # f0 critical, removed vs reference
    variant = {k: v for k, v in current.items() if k != "f1"}  # variant lacks f0 and f1 (accepted earlier)
    c = compare(fp(current), fp(BASE), [fp(variant)], threshold=0.8, critical_fields={"f0": "network.src_ip"})
    assert c.is_drift is False
    assert c.matched == "variant:0"


def test_exact_variant_match_is_normal_with_no_changes():
    variant = {"a": "1", "b": "2"}
    c = compare(fp(variant), fp(BASE), [fp(variant)], threshold=T, critical_fields={"f0": "network.src_ip"})
    assert c.is_drift is False
    assert c.matched == "variant:0"
    assert c.similarity == 1.0
    assert a.classify_changes(c.differences) == []


# --- O: recommendations -------------------------------------------------------------------


def test_recommendations_order_and_mapping():
    types = [a.FIELD_ADDITION, a.FIELD_REMOVAL, a.FIELD_TYPE_CHANGE, a.FIELD_ORDER_CHANGE, a.FORMAT_DRIFT, a.MULTIPLE_STRUCTURAL_CHANGE]
    assert a.recommend_actions(types, [{"field": "x"}]) == [
        a.REVIEW_CRITICAL_FIELD_CHANGE,
        a.REVIEW_FORMAT_DRIFT,
        a.REVIEW_REMOVED_FIELDS,
        a.REVIEW_TYPE_CHANGES,
        a.REVIEW_ADDED_FIELDS,
        a.REVIEW_FIELD_ORDER,
    ]
    assert a.recommend_actions([], None) == []


# --- M: adapter-fallback evidence ---------------------------------------------------------


def test_fallback_near_miss_same_format():
    ev = a.fallback_evidence(adapter(), event_format="syslog", parsed_fields={"app_name": "AcmeFW"},
                             raw_event="<1>Jan 1 00:00:00 h AcmeFW: x")
    assert [e["reason"] for e in ev] == [a.EVIDENCE_MATCH_FIELD_NEAR_MISS, a.EVIDENCE_VENDOR_SIGNATURE_IN_RAW]


def test_fallback_identity_field_in_other_format():
    ev = a.fallback_evidence(adapter(), event_format="json", parsed_fields={"app_name": "ACMEFW"},
                             raw_event='{"app_name": "ACMEFW"}')
    assert [e["reason"] for e in ev] == [a.EVIDENCE_IDENTITY_FIELD_IN_OTHER_FORMAT, a.EVIDENCE_VENDOR_SIGNATURE_IN_RAW]


def test_fallback_signature_only_in_raw():
    ev = a.fallback_evidence(adapter(), event_format="syslog", parsed_fields={"app_name": "sshd"},
                             raw_event="<1>Jan 1 00:00:00 h sshd: forwarded from acmefw")
    assert [e["reason"] for e in ev] == [a.EVIDENCE_VENDOR_SIGNATURE_IN_RAW]


def test_fallback_no_evidence_for_unrelated_event():
    assert a.fallback_evidence(adapter(), event_format="syslog", parsed_fields={"app_name": "sshd"},
                               raw_event="<1>Jan 1 00:00:00 h sshd: login ok") == []


def test_fallback_short_tokens_are_not_searched_in_raw():
    short = adapter(match={"format": "syslog", "field": "app_name", "contains": "FW"})
    assert a.fallback_evidence(short, event_format="syslog", parsed_fields={}, raw_event="some FW text") == []


def test_fallback_is_deterministic():
    kwargs = dict(event_format="syslog", parsed_fields={"app_name": "acmefw-2"}, raw_event="acmefw-2 said hi")
    assert a.fallback_evidence(adapter(), **kwargs) == a.fallback_evidence(adapter(), **kwargs)
