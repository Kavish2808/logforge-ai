"""Adaptive extension spill: lossless, deterministic, budget-respecting."""
import pytest

from app.evidence.canonical import canonical_bytes, canonical_sha256
from app.evidence.spill import INLINE, SPILLED, key_signature, merge, split_extensions


def ext(n: int, width: int = 10) -> dict:
    return {f"field_{i:04d}": "v" * width + str(i) for i in range(n)}


def test_small_extensions_stay_inline_unchanged():
    e = ext(5)
    r = split_extensions(e, max_bytes=8192, max_fields=64)
    assert r.mode == INLINE and r.inline == e and r.overflow == {} and r.overflow_sha256 is None


@pytest.mark.parametrize("n,max_bytes,max_fields", [(200, 1024, 1000), (80, 100_000, 10), (3, 256, 1), (500, 300, 50)])
def test_spill_is_lossless_disjoint_and_ordered(n, max_bytes, max_fields):
    e = ext(n)
    r = split_extensions(e, max_bytes=max_bytes, max_fields=max_fields)
    assert r.mode == SPILLED
    assert set(r.inline).isdisjoint(r.overflow)
    assert merge(r.inline, r.overflow, r.overflow_key_order) == e
    assert list(merge(r.inline, r.overflow, r.overflow_key_order)) == list(e)  # original order restored
    assert len(canonical_bytes(r.inline)) <= max_bytes and len(r.inline) <= max_fields
    assert r.overflow_sha256 == canonical_sha256(r.overflow)
    assert r.metadata()["inline_field_count"] + r.metadata()["overflow_field_count"] == n


def test_inline_part_is_a_prefix_of_the_original_order():
    e = ext(100)
    r = split_extensions(e, max_bytes=500, max_fields=1000)
    assert list(r.inline) == list(e)[: len(r.inline)]


def test_spill_is_deterministic():
    e = ext(300)
    a = split_extensions(e, max_bytes=2000, max_fields=20)
    b = split_extensions(dict(e), max_bytes=2000, max_fields=20)
    assert a.metadata() == b.metadata() and a.overflow == b.overflow


def test_nested_and_unicode_values_survive():
    e = {"a": {"deep": [1, 2, {"x": "ü✓"}]}, "b": None, "c": 3.5, "d": True, "e": "日本語" * 200}
    r = split_extensions(e, max_bytes=256, max_fields=64)
    assert r.mode == SPILLED and merge(r.inline, r.overflow, r.overflow_key_order) == e


def test_one_oversized_value_goes_entirely_to_overflow():
    e = {"huge": "x" * 50_000}
    r = split_extensions(e, max_bytes=1024, max_fields=64)
    assert r.inline == {} and r.overflow == e


def test_merge_refuses_overlap():
    with pytest.raises(ValueError):
        merge({"a": 1}, {"a": 2})


def test_key_signature_ignores_values_and_order():
    assert key_signature(["b", "a"]) == key_signature(["a", "b"]) != key_signature(["a", "c"])
