"""Phase 5 additions to the structural fingerprint (field_types), plus
regression checks that the frozen Phase 0-4 keys are byte-for-byte unchanged."""
import hashlib

from app.pipeline.fingerprint.structural import compute_field_types, compute_fingerprint


def test_field_types_json_type_names():
    types = compute_field_types(
        {"s": "x", "i": 1, "f": 1.5, "b": True, "n": None, "o": {"a": 1}, "l": [1]}
    )
    assert types == {
        "s": "string",
        "i": "integer",
        "f": "number",
        "b": "boolean",
        "n": "null",
        "o": "object",
        "l": "array",
    }


def test_fingerprint_includes_field_types():
    fp = compute_fingerprint({"a": 1, "b": "x"})
    assert fp["field_types"] == {"a": "integer", "b": "string"}


def test_regression_frozen_keys_unchanged():
    fp = compute_fingerprint({"b": 1, "a": 2, "c": 3})
    assert fp["field_set"] == ["a", "b", "c"]
    assert fp["field_order"] == ["b", "a", "c"]
    assert fp["field_count"] == 3
    # Phase 0-4 signature algorithm: sha256 of comma-joined sorted names.
    assert fp["signature"] == hashlib.sha256(b"a,b,c").hexdigest()


def test_regression_signature_hardcoded():
    # Digests produced by the frozen Phase 0-4 implementation (commit 1b57d0f).
    assert (
        compute_fingerprint({"b": 1, "a": 2, "c": 3})["signature"]
        == "205830ca5b23bbe39ab510cfddc1dff2d9842e38b5fa7b7c48cd4ca7e44f92a1"
    )
    assert (
        compute_fingerprint({"a": 1, "b": 2})["signature"]
        == "1eb7c54d52831bbfe8942af0b1c56b7409523a59ed6ca99c1174fef7eb32c1b5"
    )
    assert (
        compute_fingerprint({})["signature"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_signature_ignores_field_types():
    assert compute_fingerprint({"a": 1})["signature"] == compute_fingerprint({"a": {"x": 1}})["signature"]
