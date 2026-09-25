"""Unit tests for the deterministic structural drift comparator (no DB)."""
import pytest

from app.pipeline.drift import comparator
from app.pipeline.drift.comparator import compare
from app.pipeline.fingerprint.structural import compute_fingerprint

BASE_FIELDS = {f"f{i}": "v" for i in range(8)}  # 8 string fields, f0..f7
THRESHOLD = 0.85


def fp(fields: dict) -> dict:
    return compute_fingerprint(fields)


def test_weights_sum_to_one():
    total = (
        comparator.WEIGHT_FIELD_SET
        + comparator.WEIGHT_FIELD_ORDER
        + comparator.WEIGHT_FIELD_COUNT
        + comparator.WEIGHT_FIELD_TYPES
    )
    assert total == pytest.approx(1.0)


def test_identical_structure_is_normal_with_full_similarity():
    result = compare(fp(BASE_FIELDS), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.similarity == 1.0
    assert result.is_drift is False
    assert result.matched == "reference"
    assert result.differences["added_fields"] == []
    assert result.differences["removed_fields"] == []
    assert result.differences["order_changed"] is False
    assert result.differences["type_changes"] == {}


def test_values_do_not_matter_only_structure():
    other_values = {k: "something else entirely" for k in BASE_FIELDS}
    assert compare(fp(other_values), fp(BASE_FIELDS), threshold=THRESHOLD).similarity == 1.0


def test_one_added_field_stays_normal_and_is_reported():
    current = {**BASE_FIELDS, "new_field": "x"}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.similarity == pytest.approx(0.916, abs=0.001)
    assert result.is_drift is False
    assert result.differences["added_fields"] == ["new_field"]
    assert result.differences["field_count"] == {"baseline": 8, "current": 9}


def test_two_added_fields_cross_default_threshold():
    current = {**BASE_FIELDS, "new1": "x", "new2": "y"}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.similarity == pytest.approx(0.8478, abs=0.001)
    assert result.is_drift is True
    assert result.differences["added_fields"] == ["new1", "new2"]


def test_removed_fields_are_reported():
    current = {k: v for k, v in BASE_FIELDS.items() if k not in ("f3", "f5")}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.differences["removed_fields"] == ["f3", "f5"]
    assert result.differences["added_fields"] == []
    assert result.components["field_count"] == 0.75
    assert result.is_drift is True


def test_adjacent_swap_is_reported_but_normal():
    keys = list(BASE_FIELDS)
    keys[0], keys[1] = keys[1], keys[0]
    current = {k: "v" for k in keys}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.differences["order_changed"] is True
    assert result.components["field_set"] == 1.0
    assert result.is_drift is False


def test_full_order_reversal_is_drift():
    current = {k: "v" for k in reversed(list(BASE_FIELDS))}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    # Same set, same count, same types: only the ordering changed.
    assert result.components["field_set"] == 1.0
    assert result.components["field_count"] == 1.0
    assert result.differences["order_changed"] is True
    assert result.is_drift is True


def test_same_signature_different_order_is_not_a_fast_path_match():
    # Phase 0-4 signature is order-insensitive; drift must still see order.
    current = {k: "v" for k in reversed(list(BASE_FIELDS))}
    assert fp(current)["signature"] == fp(BASE_FIELDS)["signature"]
    assert compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD).similarity < 1.0


def test_type_change_is_detected():
    current = {**BASE_FIELDS, "f2": {"nested": True}}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.differences["type_changes"] == {"f2": {"baseline": "string", "current": "object"}}
    assert result.components["field_types"] == pytest.approx(7 / 8, abs=0.0001)


def test_null_is_compatible_with_any_type():
    current = {**BASE_FIELDS, "f2": None}
    result = compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD)
    assert result.differences["type_changes"] == {}
    assert result.similarity == 1.0


def test_legacy_baseline_without_field_types_is_type_neutral():
    legacy = fp(BASE_FIELDS)
    del legacy["field_types"]
    current = {**BASE_FIELDS, "f2": {"nested": True}}
    result = compare(fp(current), legacy, threshold=THRESHOLD)
    assert result.components["field_types"] == 1.0
    assert result.differences["type_changes"] == {}


def test_score_equal_to_threshold_is_normal():
    current = {**BASE_FIELDS, "new1": "x", "new2": "y"}
    score = compare(fp(current), fp(BASE_FIELDS), threshold=0.0).similarity
    assert compare(fp(current), fp(BASE_FIELDS), threshold=score).is_drift is False


def test_threshold_is_respected():
    current = {**BASE_FIELDS, "new1": "x", "new2": "y"}
    assert compare(fp(current), fp(BASE_FIELDS), threshold=0.70).is_drift is False
    assert compare(fp(current), fp(BASE_FIELDS), threshold=0.95).is_drift is True
    assert compare(fp(BASE_FIELDS), fp(BASE_FIELDS), threshold=1.0).is_drift is False


def test_matching_accepted_variant_is_normal():
    variant_fields = {"a": "1", "b": "2", "c": "3"}
    result = compare(fp(variant_fields), fp(BASE_FIELDS), [fp(variant_fields)], threshold=THRESHOLD)
    assert result.is_drift is False
    assert result.similarity == 1.0
    assert result.matched == "variant:0"


def test_best_candidate_wins():
    variant = {**BASE_FIELDS, "extra": "x"}
    current = {**BASE_FIELDS, "extra": "x"}
    result = compare(fp(current), fp(BASE_FIELDS), [fp({"z": 1}), fp(variant)], threshold=THRESHOLD)
    assert result.matched == "variant:1"
    assert result.similarity == 1.0


def test_format_change_always_drifts():
    result = compare(
        fp(BASE_FIELDS),
        fp(BASE_FIELDS),
        threshold=0.0,
        current_format="json",
        baseline_format="syslog",
    )
    assert result.is_drift is True
    assert result.differences["format_changed"] == {"baseline": "syslog", "current": "json"}


def test_empty_structures_are_identical():
    result = compare(fp({}), fp({}), threshold=THRESHOLD)
    assert result.similarity == 1.0
    assert result.is_drift is False


def test_empty_versus_populated_is_drift():
    assert compare(fp({}), fp(BASE_FIELDS), threshold=THRESHOLD).is_drift is True


def test_comparison_is_deterministic():
    current = {**BASE_FIELDS, "new1": "x", "f2": 5}
    results = {compare(fp(current), fp(BASE_FIELDS), threshold=THRESHOLD).similarity for _ in range(20)}
    assert len(results) == 1
