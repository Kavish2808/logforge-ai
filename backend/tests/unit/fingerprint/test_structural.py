from app.pipeline.fingerprint.structural import compute_fingerprint


def test_field_order_matches_input_order():
    fp = compute_fingerprint({"b": 1, "a": 2, "c": 3})
    assert fp["field_order"] == ["b", "a", "c"]


def test_field_set_is_sorted():
    fp = compute_fingerprint({"b": 1, "a": 2, "c": 3})
    assert fp["field_set"] == ["a", "b", "c"]


def test_field_count():
    fp = compute_fingerprint({"a": 1, "b": 2})
    assert fp["field_count"] == 2


def test_signature_is_stable_for_same_field_set_regardless_of_order():
    fp1 = compute_fingerprint({"a": 1, "b": 2})
    fp2 = compute_fingerprint({"b": 20, "a": 10})
    assert fp1["signature"] == fp2["signature"]


def test_signature_differs_for_different_field_sets():
    fp1 = compute_fingerprint({"a": 1, "b": 2})
    fp2 = compute_fingerprint({"a": 1, "c": 2})
    assert fp1["signature"] != fp2["signature"]


def test_empty_fields():
    fp = compute_fingerprint({})
    assert fp["field_count"] == 0
    assert fp["field_set"] == []
    assert fp["field_order"] == []
