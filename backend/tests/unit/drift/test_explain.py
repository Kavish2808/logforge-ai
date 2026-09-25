"""N: the human-readable drift report is derived deterministically from
the structured record, for both stored dicts and the API model."""
from app.pipeline.drift.explain import render_explanation
from app.schema.ocsf import DriftMetadata

DRIFT_RECORD = {
    "status": "DRIFT",
    "source_key": "paloalto_cef",
    "baseline_version": 3,
    "baseline_origin": "human_review",
    "matched": "reference",
    "similarity": 0.7451,
    "threshold": 0.85,
    "severity": "HIGH",
    "severity_score": 14,
    "change_types": ["FIELD_ADDITION", "FIELD_REMOVAL", "FIELD_TYPE_CHANGE", "FIELD_ORDER_CHANGE", "MULTIPLE_STRUCTURAL_CHANGE"],
    "decision_reasons": ["SIMILARITY_BELOW_THRESHOLD", "CRITICAL_FIELD_CHANGED"],
    "differences": {
        "added_fields": ["policy_id", "threat_score"],
        "removed_fields": ["old_action"],
        "field_count": {"baseline": 10, "current": 11},
        "order_changed": True,
        "type_changes": {"dpt": {"baseline": "string", "current": "integer"}},
    },
    "critical_field_changes": [
        {"field": "dpt", "target": "network.dst_port", "change": "type_changed", "baseline_type": "string", "current_type": "integer"}
    ],
    "recommended_actions": ["REVIEW_CRITICAL_FIELD_CHANGE", "REVIEW_REMOVED_FIELDS"],
    "reonboarding_required": True,
}

EXPECTED = """DRIFT DETECTED
Source: paloalto_cef
Baseline Version: 3 (human_review)
Matched: reference
Similarity: 0.7451
Threshold: 0.85
Severity: HIGH (score 14)
Change types: FIELD_ADDITION, FIELD_REMOVAL, FIELD_TYPE_CHANGE, FIELD_ORDER_CHANGE, MULTIPLE_STRUCTURAL_CHANGE
Decision: SIMILARITY_BELOW_THRESHOLD, CRITICAL_FIELD_CHANGED

Changes:
+ policy_id
+ threat_score
- old_action
~ dpt type changed string → integer
↕ field order changed
! critical field type changed: dpt (network.dst_port) string → integer

Recommendation:
REVIEW_CRITICAL_FIELD_CHANGE, REVIEW_REMOVED_FIELDS
Human review required."""


def test_drift_explanation_exact_text():
    assert render_explanation(DRIFT_RECORD) == EXPECTED


def test_model_and_dict_render_identically():
    model = DriftMetadata.model_validate(DRIFT_RECORD)
    assert model.explanation == EXPECTED
    assert model.model_dump()["explanation"] == EXPECTED


def test_reviewed_drift_shows_resolution_instead_of_review_required():
    record = {
        **DRIFT_RECORD,
        "reonboarding_required": False,
        "review": {"resolution": "accepted_variant", "reviewed_at": "2026-09-25T10:00:00+00:00", "baseline_version": 4},
    }
    text = render_explanation(record)
    assert "Reviewed: accepted_variant at 2026-09-25T10:00:00+00:00 (baseline v4)." in text
    assert "Human review required." not in text


def test_possible_format_drift_explanation():
    text = render_explanation(
        {
            "status": "POSSIBLE_FORMAT_DRIFT",
            "source_key": "fortinet",
            "current_adapter": "syslog_generic",
            "baseline_version": 1,
            "similarity": 0.5,
            "threshold": 0.85,
            "severity": "HIGH",
            "severity_score": 9,
            "change_types": ["FORMAT_DRIFT"],
            "evidence": [{"reason": "MATCH_FIELD_NEAR_MISS", "detail": "observed 'FortiGate'"}],
            "recommended_actions": ["REVIEW_FORMAT_DRIFT"],
            "reonboarding_required": True,
        }
    )
    assert text.startswith("POSSIBLE FORMAT DRIFT\nPreviously known source: fortinet\nCurrent adapter: syslog_generic")
    assert "Similarity to known source baseline: 0.5000" in text
    assert "* MATCH_FIELD_NEAR_MISS: observed 'FortiGate'" in text
    assert "REVIEW_FORMAT_DRIFT" in text


def test_format_change_and_critical_removal_lines():
    text = render_explanation(
        {
            "status": "DRIFT",
            "source_key": "s",
            "differences": {"format_changed": {"baseline": "syslog", "current": "json"}},
            "critical_field_changes": [{"field": "src", "target": "network.src_ip", "change": "removed"}],
        }
    )
    assert "⇄ format changed syslog → json" in text
    assert "! critical field removed: src (network.src_ip)" in text


def test_normal_baseline_and_error_explanations():
    assert render_explanation({"status": "NORMAL", "source_key": "s", "similarity": 1.0, "threshold": 0.85}).startswith(
        "NO DRIFT (NORMAL)\nSource: s"
    )
    assert render_explanation({"status": "BASELINE_CREATED", "source_key": "s", "baseline_version": 1}).startswith(
        "BASELINE CREATED"
    )
    err = render_explanation({"status": "ERROR", "source_key": "s", "error": "boom"})
    assert "Error: boom" in err and "no drift decision was made" in err
