"""Proposal schema, evidence review, adapter conversion and sandbox decisions."""
import copy
import json

import pytest
from pydantic import ValidationError

from app.adapters.loader import get_adapter_registry
from app.onboarding import sandbox
from app.onboarding.analysis import analyze_samples
from app.onboarding.proposal import (
    ALLOWED_TARGETS,
    AdapterProposal,
    review_proposal,
    suggested_adapter_id,
    to_adapter,
)
from app.onboarding.providers import build_offline_proposal
from tests.unit.onboarding.test_analysis import kv_samples

THRESHOLDS = dict(min_match_rate=0.9, reject_below_match_rate=0.5, min_mapping_coverage=0.3)


@pytest.fixture()
def evidence():
    samples = kv_samples()
    analysis = analyze_samples(samples)
    return samples, analysis, build_offline_proposal(analysis)


def evaluate(samples, analysis, proposal_dict):
    proposal = AdapterProposal.model_validate(proposal_dict)
    review = review_proposal(proposal, analysis)
    metrics = None
    if not review["issues"]:
        candidate = to_adapter(proposal, review["accepted"], adapter_id=suggested_adapter_id(proposal), version=1, description="t")
        metrics = sandbox.run_sandbox(samples, candidate, get_adapter_registry())
    return review, metrics, sandbox.decide(review, metrics, **THRESHOLDS)


def test_offline_proposal_passes(evidence):
    samples, analysis, p = evidence
    review, metrics, (result, reasons) = evaluate(samples, analysis, p)
    assert result == "PASSED"
    assert metrics["match_rate"] == 1.0 and metrics["matched_samples"] == 12
    assert metrics["mapping_presence"]["rule"] == 4
    assert "12/12 samples matched the proposed adapter (0 did not)." in reasons
    assert reasons[-1].startswith("Eligible for human approval")


def test_schema_rejects_extra_keys_code_and_unknown_strategies(evidence):
    _, _, p = evidence
    for bad in (
        {**p, "code": "import os"},
        {**p, "parser": {**p["parser"], "strategy": "python"}},
        {**p, "parser": {**p["parser"], "pattern": "(a+)+$"}},
        {**p, "mappings": [{**p["mappings"][0], "transform": "lambda v: v"}]},
        {**p, "timestamp_format": "%Y$(rm -rf /)"},
        {**p, "match": {"field": "vendor"}},
        {**p, "match": {"field": "vendor", "equals": "A", "contains": "ABC"}},
        {**p, "format": "xml"},
    ):
        with pytest.raises(ValidationError):
            AdapterProposal.model_validate(bad)


def test_unknown_target_is_rejected_and_field_preserved(evidence):
    samples, analysis, p = evidence
    p = copy.deepcopy(p)
    p["mappings"].append({"raw_field": "vendor", "target": "network.src_ipx", "confidence": 0.9, "evidence": ""})
    review, metrics, (result, reasons) = evaluate(samples, analysis, p)
    assert [r["reason"] for r in review["rejected"]] == ["unknown target 'network.src_ipx' (not in the universal schema)"]
    assert result == "NEEDS_REVIEW"
    assert "vendor" in metrics["unknown_fields"]  # preserved under extensions


def test_invented_field_and_duplicates_are_rejected(evidence):
    samples, analysis, p = evidence
    p = copy.deepcopy(p)
    p["mappings"] += [
        {"raw_field": "not_in_samples", "target": "session_id", "confidence": 0.9, "evidence": ""},
        {"raw_field": "vendor", "target": "network.src_ip", "confidence": 0.9, "evidence": ""},
    ]
    review, _, (result, _) = evaluate(samples, analysis, p)
    reasons = [r["reason"] for r in review["rejected"]]
    assert "raw field 'not_in_samples' does not occur in the samples" in reasons
    assert "target 'network.src_ip' is already mapped by another field" in reasons
    assert result == "NEEDS_REVIEW"


def test_proposal_level_issues_reject_without_executing(evidence):
    samples, analysis, p = evidence
    for change, fragment in (
        ({"format": "json", "parser": {"strategy": "native"}}, "contradicts the evidence"),
        ({"ocsf_class_uid": 9999}, "not an allowed onboarding class"),
        ({"match": {"field": "nope", "equals": "x"}}, "does not occur in the samples"),
        ({"mappings": []}, "No valid field mappings remain"),
    ):
        review, metrics, (result, reasons) = evaluate(samples, analysis, {**p, **change})
        assert result == "REJECTED" and metrics is None
        assert any(fragment in r for r in reasons)
        assert "not executed in the sandbox" in reasons[-1]


def test_inconsistent_samples_lower_match_rate(evidence):
    samples, analysis, p = evidence
    mixed = samples[:10] + [s.replace("vendor=ACMEFW", "vendor=OTHER") for s in samples[10:]]
    review, metrics, (result, reasons) = evaluate(mixed, analyze_samples(mixed), p)
    assert metrics["match_rate"] == pytest.approx(10 / 12, abs=1e-4)
    assert result == "NEEDS_REVIEW"
    half = samples[:5] + [s.replace("vendor=ACMEFW", "vendor=OTHER") for s in samples[5:]]
    _, metrics, (result, _) = evaluate(half, analyze_samples(half), p)
    assert metrics["match_rate"] < 0.5 and result == "REJECTED"


def test_threshold_boundaries_are_configurable():
    review = {"issues": [], "rejected": []}
    metrics = {"total_samples": 10, "matched_samples": 9, "failed_samples": 1, "match_rate": 0.9, "mapping_coverage": 0.3,
               "claimed_by_existing": {}, "warning_count": 0, "unknown_field_count": 0, "unknown_fields": []}
    assert sandbox.decide(review, metrics, **THRESHOLDS)[0] == "PASSED"  # exactly at both thresholds
    assert sandbox.decide(review, metrics, **{**THRESHOLDS, "min_match_rate": 0.95})[0] == "NEEDS_REVIEW"
    assert sandbox.decide(review, metrics, **{**THRESHOLDS, "min_mapping_coverage": 0.31})[0] == "NEEDS_REVIEW"
    assert sandbox.decide(review, metrics, **{**THRESHOLDS, "reject_below_match_rate": 0.95})[0] == "REJECTED"
    warn = {**metrics, "warning_count": 2}
    assert sandbox.decide(review, warn, **THRESHOLDS)[0] == "NEEDS_REVIEW"


def test_known_source_samples_are_rejected():
    samples = [f"<166>Jan 18 12:05:{i:02d} ciscoasa %ASA-6-302013: Built connection {i}" for i in range(10)]
    analysis = analyze_samples(samples)
    proposal = {
        "vendor": "Someone", "product": "Else", "format": "syslog", "parser": {"strategy": "native"},
        "match": {"field": "hostname", "equals": "ciscoasa"}, "ocsf_class_uid": 4001,
        "mappings": [{"raw_field": "message", "target": "event_message", "confidence": 0.9, "evidence": ""}],
    }
    _, metrics, (result, reasons) = evaluate(samples, analysis, proposal)
    assert metrics["claimed_by_existing"] == {"cisco_asa": 10}
    assert result == "REJECTED" and any("drift review" in r for r in reasons)


def test_to_adapter_uses_special_fields_and_is_declarative(evidence):
    _, analysis, p = evidence
    proposal = AdapterProposal.model_validate(p)
    adapter = to_adapter(proposal, review_proposal(proposal, analysis)["accepted"], adapter_id="acmefw_acmefw",
                         version=3, description="t")
    assert adapter.version == "3" and adapter.source == "onboarded"
    assert adapter.event_action_field == "action" and adapter.severity_field == "sev" and adapter.timestamp_field == "ts"
    assert adapter.resolved_field_map()["srcport"].type == "int"
    assert adapter.parser.strategy == "kv"
    assert set(json.loads(adapter.model_dump_json())) >= {"parser", "match", "field_map"}


def test_allowed_targets_come_from_the_universal_schema():
    assert {"network.src_ip", "network.dst_port", "user.name", "process.pid", "severity", "event_action"} <= ALLOWED_TARGETS
    assert "network.src_ipx" not in ALLOWED_TARGETS


def test_suggested_adapter_id():
    base = {"format": "kv", "parser": {"strategy": "kv"}, "match": {"field": "a", "equals": "b"}, "ocsf_class_uid": 4001}
    assert suggested_adapter_id(AdapterProposal.model_validate({**base, "vendor": "ACME Corp", "product": "Gate-Way 2"})) == "acme_corp_gate_way_2"
    assert suggested_adapter_id(AdapterProposal.model_validate({**base, "vendor": "3Com", "product": "X"})) == "src_3com_x"


def test_sandbox_is_deterministic(evidence):
    samples, analysis, p = evidence
    assert evaluate(samples, analysis, p)[1] == evaluate(samples, analysis, p)[1]
