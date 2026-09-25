import pytest

from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.json_parser import JSONParser
from tests.conftest import load_fixture

parser = JSONParser()


def test_parses_valid_json_object():
    result = parser.parse(load_fixture("json_samples/valid_login.json"))
    assert result.format_detected == "json"
    assert result.fields["message"] == "User login successful"
    assert result.fields["user"] == "jdoe"
    assert result.fields["src_ip"] == "10.0.0.5"
    assert result.fields["src_port"] == 51234  # native JSON int, not stringified
    assert result.warnings == []


def test_preserves_unknown_nested_and_list_fields():
    result = parser.parse(load_fixture("json_samples/valid_unknown_fields.json"))
    assert result.fields["custom_field_one"] == "abc"
    assert result.fields["custom_nested"] == {"a": 1, "b": 2}
    assert result.fields["custom_list"] == [1, 2, 3]


def test_malformed_json_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse(load_fixture("json_samples/malformed.json"))


def test_json_array_top_level_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse("[1, 2, 3]")


def test_json_scalar_top_level_raises_parser_error():
    with pytest.raises(ParserError):
        parser.parse('"just a string"')
