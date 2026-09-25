from app.pipeline.normalizer.extension_handler import compute_extensions


def test_unconsumed_fields_are_preserved():
    parsed = {"a": 1, "b": 2, "c": 3}
    extensions = compute_extensions(parsed, consumed_keys={"a"})
    assert extensions == {"b": 2, "c": 3}


def test_all_consumed_yields_empty_extensions():
    parsed = {"a": 1, "b": 2}
    extensions = compute_extensions(parsed, consumed_keys={"a", "b"})
    assert extensions == {}


def test_none_consumed_preserves_everything():
    parsed = {"a": 1, "b": 2}
    extensions = compute_extensions(parsed, consumed_keys=set())
    assert extensions == parsed
    assert extensions is not parsed  # a new dict, not the same reference


def test_nothing_is_ever_dropped_only_relocated():
    parsed = {"a": 1, "b": {"nested": True}, "c": [1, 2, 3]}
    extensions = compute_extensions(parsed, consumed_keys={"a"})
    assert set(extensions.keys()) == {"b", "c"}
    assert extensions["b"] == {"nested": True}
    assert extensions["c"] == [1, 2, 3]
