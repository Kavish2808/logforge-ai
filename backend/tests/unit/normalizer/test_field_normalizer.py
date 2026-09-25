from app.pipeline.normalizer.field_normalizer import coerce_type, parse_timestamp


def test_coerce_type_int():
    value, warning = coerce_type("443", "int")
    assert value == 443
    assert warning is None


def test_coerce_type_float():
    value, warning = coerce_type("3.14", "float")
    assert value == 3.14
    assert warning is None


def test_coerce_type_bool_truthy_values():
    for raw in ("true", "1", "yes", "accept", "allow", "TRUE"):
        value, warning = coerce_type(raw, "bool")
        assert value is True
        assert warning is None


def test_coerce_type_bool_falsy_values():
    for raw in ("false", "0", "no", "deny"):
        value, warning = coerce_type(raw, "bool")
        assert value is False
        assert warning is None


def test_coerce_type_invalid_int_falls_back_to_original_value_with_warning():
    value, warning = coerce_type("not-a-number", "int")
    assert value == "not-a-number"
    assert warning is not None
    assert "not-a-number" in warning


def test_coerce_type_none_passthrough():
    value, warning = coerce_type(None, "int")
    assert value is None
    assert warning is None


def test_coerce_type_no_type_hint_passthrough():
    value, warning = coerce_type("raw", None)
    assert value == "raw"
    assert warning is None


def test_parse_timestamp_iso8601():
    dt, warning = parse_timestamp("2026-01-18T12:00:00Z")
    assert warning is None
    assert dt.year == 2026 and dt.month == 1 and dt.day == 18
    assert dt.tzinfo is not None


def test_parse_timestamp_rfc3164_style_defaults_missing_year():
    dt, warning = parse_timestamp("Jan 18 12:00:00")
    assert warning is None
    assert dt.month == 1 and dt.day == 18 and dt.hour == 12


def test_parse_timestamp_with_explicit_format():
    dt, warning = parse_timestamp("18/01/2026 12:00", fmt="%d/%m/%Y %H:%M")
    assert warning is None
    assert dt.year == 2026 and dt.month == 1 and dt.day == 18


def test_parse_timestamp_invalid_returns_warning_not_exception():
    dt, warning = parse_timestamp("###totally-not-a-timestamp###")
    assert dt is None
    assert warning is not None


def test_parse_timestamp_none_input():
    dt, warning = parse_timestamp(None)
    assert dt is None and warning is None
