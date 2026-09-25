"""Phase 6 unit tests: delta schema/review/apply, the deterministic learning
engine, and the learning sandbox decision (no DB)."""
import json

import pytest
from pydantic import ValidationError

from app.adapters.loader import AdapterRegistry, get_adapter_registry
from app.learning import validation as lv
from app.learning.delta import LearningDelta, apply_delta, mapping_diff, normalized_mapping, review_delta
from app.learning.engine import build_delta, field_evidence
from app.schema.adapter import AdapterMapping

ADAPTER = AdapterMapping.model_validate({
    "id": "acme_fw", "vendor": "ACME", "product": "FW", "format": "kv", "version": "1", "source": "onboarded",
    "parser": {"strategy": "kv"}, "match": {"format": "kv", "field": "vendor", "equals": "ACME"},
    "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
    "event_action_field": "action", "severity_field": "sev",
    "field_map": {"srcip": {"target": "network.src_ip"}, "dstip": {"target": "network.dst_ip"},
                  "dstport": {"target": "network.dst_port", "type": "int"}, "msg": {"target": "event_message"}},
})
CRITICAL = {"action": "event_action", "sev": "severity", "srcip": "network.src_ip", "dstip": "network.dst_ip", "dstport": "network.dst_port"}


def log(i, **over):
    f = {"vendor": "ACME", "srcip": f"10.0.0.{i + 1}", "dstip": "8.8.8.8", "dstport": "443", "action": "allow", "sev": "high", "msg": f"m{i}"}
    f.update(over)
    return " ".join(f"{k}={v}" for k, v in f.items() if v is not None)


def learn(drifted_raws, historical_raws, differences, change_types):
    drifted, n = field_evidence(ADAPTER, drifted_raws)
    historical, _ = field_evidence(ADAPTER, historical_raws)
    return build_delta(ADAPTER, differences, change_types, drifted, n, historical, CRITICAL)


HIST = [log(i) for i in range(10)]


# --- engine ----------------------------------------------------------------------------------------------


def test_addition_high_and_medium_confidence():
    drifted = [log(i) + f" username=u{i}" + (" session=s1" if i < 5 else "") for i in range(10)]
    out = learn(drifted, HIST, {"added_fields": ["username", "session"]}, ["FIELD_ADDITION"])
    adds = {m.raw_field: (m.target, m.confidence) for m in out["delta"].add_mappings}
    assert adds == {"username": ("user.name", "HIGH"), "session": ("session_id", "MEDIUM")}
    assert out["risk"] == "MEDIUM"


def test_addition_without_evidence_is_preserved_not_invented():
    out = learn([log(i) + " tenant=t1" for i in range(5)], HIST, {"added_fields": ["tenant"]}, ["FIELD_ADDITION"])
    assert out["delta"].add_mappings == []
    assert out["delta"].unresolved[0].kind == "PRESERVED_IN_EXTENSIONS"
    assert not out["delta"].changes_mapping()


def test_addition_with_wrong_value_class_is_not_mapped():
    out = learn([log(i) + " src_port=abc" for i in range(5)], HIST, {"added_fields": ["src_port"]}, ["FIELD_ADDITION"])
    assert out["delta"].add_mappings == [] and "values are string" in out["delta"].unresolved[0].reason


def test_rename_by_naming_convention_is_high():
    drifted = [log(i, srcip=None) + f" source_ip=10.0.0.{i}" for i in range(6)]
    out = learn(drifted, HIST, {"added_fields": ["source_ip"], "removed_fields": ["srcip"]}, ["FIELD_ADDITION", "FIELD_REMOVAL"])
    r = out["delta"].remaps[0]
    assert (r.from_field, r.to_field, r.target, r.confidence) == ("srcip", "source_ip", "network.src_ip", "HIGH")
    assert "SEMANTIC_REMAP" in out["modes"] and out["risk"] == "HIGH"


def test_rename_by_position_is_medium_and_ambiguity_is_not_guessed():
    drifted = [f"vendor=ACME weird_x=10.0.0.{i} dstip=8.8.8.8 dstport=443 action=allow sev=high msg=m" for i in range(6)]
    out = learn(drifted, HIST, {"added_fields": ["weird_x"], "removed_fields": ["srcip"]}, [])
    assert out["delta"].remaps[0].confidence == "MEDIUM"
    two = [log(i, srcip=None) + f" a_ip=1.1.1.{i} b_ip=2.2.2.{i}" for i in range(6)]
    out = learn(two, HIST, {"added_fields": ["a_ip", "b_ip"], "removed_fields": ["srcip"]}, [])
    assert out["delta"].remaps == [] and out["delta"].optional_fields == ["srcip"]


def test_removal_keeps_mapping_as_optional():
    out = learn([log(i, msg=None) for i in range(5)], HIST, {"removed_fields": ["msg"]}, ["FIELD_REMOVAL"])
    assert out["delta"].optional_fields == ["msg"] and out["delta"].remove_mappings == []
    assert out["risk"] == "MEDIUM"


def test_type_change_compatible_vs_incompatible():
    json_adapter = AdapterMapping.model_validate({**ADAPTER.model_dump(), "format": "json", "parser": None,
                                                  "match": {"format": "json", "field": "vendor", "equals": "ACME"}})
    good = [json.dumps({"vendor": "ACME", "dstport": "443"}) for _ in range(3)]
    bad = [json.dumps({"vendor": "ACME", "dstport": {"p": 443}}) for _ in range(3)]
    for raws, removed in ((good, False), (bad, True)):
        drifted, n = field_evidence(json_adapter, raws)
        out = build_delta(json_adapter, {"type_changes": {"dstport": {"baseline": "integer", "current": "string"}}},
                          ["FIELD_TYPE_CHANGE"], drifted, n, {}, CRITICAL)
        assert bool(out["delta"].remove_mappings) is removed
        assert out["delta"].needs_manual_mapping() is removed


def test_order_only_learns_nothing():
    out = learn(HIST, HIST, {"order_changed": True}, ["FIELD_ORDER_CHANGE"])
    assert not out["delta"].changes_mapping() and out["risk"] == "LOW"
    assert "order-independent" in out["delta"].notes[-1]


def test_engine_is_deterministic():
    drifted = [log(i) + f" username=u{i}" for i in range(6)]
    args = (drifted, HIST, {"added_fields": ["username"]}, ["FIELD_ADDITION"])
    assert learn(*args)["delta"] == learn(*args)["delta"]


# --- delta ------------------------------------------------------------------------------------------------


def test_delta_schema_is_strict():
    for bad in ({"exec": "x"}, {"add_mappings": [{"raw_field": "a", "target": "b", "confidence": "HIGH", "code": 1}]},
                {"add_mappings": [{"raw_field": "a b", "target": "user.name", "confidence": "HIGH"}]},
                {"optional_fields": ["$(id)"]}, {"notes": ["x"], "regex": "(a+)+"}):
        with pytest.raises(ValidationError):
            LearningDelta.model_validate(bad)


def test_apply_delta_alias_remap_keeps_history_and_special_remap_replaces():
    delta = LearningDelta.model_validate({
        "remaps": [{"from_field": "srcip", "to_field": "source_ip", "target": "network.src_ip", "confidence": "HIGH"},
                   {"from_field": "action", "to_field": "act", "target": "event_action", "confidence": "HIGH"}],
        "add_mappings": [{"raw_field": "username", "target": "user.name", "confidence": "HIGH"}],
        "optional_fields": ["msg"],
    })
    new = apply_delta(ADAPTER, delta, version=2, description="t")
    assert new.version == "2" and new.source == "onboarded"
    assert new.resolved_field_map()["srcip"].target == new.resolved_field_map()["source_ip"].target == "network.src_ip"
    assert new.event_action_field == "act"
    assert new.optional_fields == ["msg", "srcip"]
    diff = mapping_diff(ADAPTER, new)
    assert "username → user.name" in diff["added"] and "action → event_action" in diff["removed"]
    assert "dstport → network.dst_port" in diff["unchanged"]


def test_review_rejects_unrelated_and_unobserved_changes():
    diff = {"added_fields": ["username"], "removed_fields": [], "type_changes": {}}
    issues = review_delta(LearningDelta.model_validate({"add_mappings": [
        {"raw_field": "username", "target": "user.name", "confidence": "HIGH"}]}), ADAPTER, diff, set())
    assert any("was not observed" in i for i in issues)
    issues = review_delta(LearningDelta.model_validate({"optional_fields": ["srcip"]}), ADAPTER, diff, {"username"})
    assert any("was not removed by the approved drift" in i for i in issues)


def test_normalized_mapping_ignores_version_and_legacy_shape():
    stored = ADAPTER.model_dump(mode="json")
    legacy = {k: v for k, v in stored.items() if k != "optional_fields"}
    assert normalized_mapping(legacy) == normalized_mapping({**stored, "version": "9", "description": "x"})


# --- P: learning sandbox thresholds ------------------------------------------------------------------------


def candidate():
    delta = LearningDelta.model_validate({"add_mappings": [{"raw_field": "username", "target": "user.name", "confidence": "HIGH"}]})
    return apply_delta(ADAPTER, delta, version=2, description="t")


def registry():
    return AdapterRegistry(get_adapter_registry().all() + [ADAPTER])


def run(matching, total=10, historical=HIST, **kw):
    drifted = [log(i) + f" username=u{i}" if i < matching else log(i, vendor="OTHER") + " username=x" for i in range(total)]
    args = dict(issues=[], needs_manual=False, risk_reasons=[], changes_mapping=True, min_match_rate=0.9,
                reject_below_match_rate=0.5, min_mapping_coverage=0.3)
    args.update(kw)
    return lv.validate_candidate(candidate(), registry(), drifted, historical, **args)


@pytest.mark.parametrize("matching,result", [(10, "PASSED"), (9, "PASSED"), (8, "NEEDS_REVIEW"), (4, "REJECTED")])
def test_p_match_rate_threshold(matching, result):
    out = run(matching)
    assert out["new_structure"]["match_rate"] == matching / 10
    assert out["result"] == result


def test_passed_includes_preservation_and_compatibility():
    out = run(10)
    assert out["preservation"] == {"samples_checked": 20, "raw_or_hash_failures": 0, "missing_normalized": 0}
    assert out["regressions"] == [] and "10/10 historical samples normalize identically" in " ".join(out["reasons"])


def test_no_historical_evidence_needs_review():
    out = run(10, historical=[])
    assert out["result"] == "NEEDS_REVIEW" and any("compatibility is unproven" in r for r in out["reasons"])


def test_unsafe_delta_is_never_executed():
    out = run(10, issues=["target not allow-listed"])
    assert out["result"] == "REJECTED" and out["new_structure"] is None


def test_manual_mapping_and_critical_absence_block_passing():
    assert run(10, needs_manual=True)["result"] == "NEEDS_REVIEW"
    assert run(10, risk_reasons=["Critical field 'srcip' (network.src_ip) is absent and no replacement is evidenced."])["result"] == "NEEDS_REVIEW"
