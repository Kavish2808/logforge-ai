"""Declarative (configuration-only) parser: correctness, limits, and
linear-time behavior on pathological input."""
import time

import pytest
from pydantic import ValidationError

from app.adapters.loader import AdapterRegistry
from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.declarative import DeclarativeParser, split_kv_pairs
from app.schema.adapter import AdapterMapping, DeclarativeParserConfig

DELIM = DeclarativeParserConfig(strategy="delimited", delimiter="|", columns=["ts", "vendor", "src", "dst", "action"])
KV = DeclarativeParserConfig(strategy="kv")


def test_delimited_parses_named_columns():
    fields = DeclarativeParser(DELIM).parse("2026-01-18T12:00:00Z|ACME|10.0.0.1|8.8.8.8|allow").fields
    assert fields == {"ts": "2026-01-18T12:00:00Z", "vendor": "ACME", "src": "10.0.0.1", "dst": "8.8.8.8", "action": "allow"}


def test_delimited_column_count_must_match():
    with pytest.raises(ParserError, match="Expected 5"):
        DeclarativeParser(DELIM).parse("a|b|c")


def test_delimited_csv_quoting():
    cfg = DeclarativeParserConfig(strategy="delimited", delimiter=",", columns=["a", "b", "c"])
    assert DeclarativeParser(cfg).parse('x,"hello, world",z').fields["b"] == "hello, world"


def test_kv_parses_pairs_and_quotes():
    fields = DeclarativeParser(KV).parse('vendor=ACME src=10.0.0.1 msg="two words" act=allow').fields
    assert fields == {"vendor": "ACME", "src": "10.0.0.1", "msg": "two words", "act": "allow"}


def test_kv_ignores_non_pairs_and_keeps_first_duplicate():
    assert split_kv_pairs("hello a=1 b=2 a=3 c=4", "whitespace", "=") == {"a": "1", "b": "2", "c": "4"}


def test_kv_other_separators():
    assert split_kv_pairs("a:1;b:2;c:3", ";", ":") == {"a": "1", "b": "2", "c": "3"}


def test_kv_min_pairs_and_unterminated_quote():
    with pytest.raises(ParserError, match="at least 3"):
        DeclarativeParser(KV).parse("a=1 b=2")
    with pytest.raises(ParserError, match="Unterminated"):
        DeclarativeParser(KV).parse('a=1 b=2 c="oops')


def test_multiline_and_oversized_rejected():
    with pytest.raises(ParserError, match="single-line"):
        DeclarativeParser(KV).parse("a=1 b=2\nc=3")
    with pytest.raises(ParserError, match="exceeds"):
        DeclarativeParser(KV).parse("a=" + "x" * 70_000)


@pytest.mark.parametrize(
    "payload",
    [
        "a" * 63_000,                       # one huge token without '='
        "a=" * 21_000,                      # many empty-valued pairs
        '"' + "x" * 62_000 + '"',            # huge quoted token
        "=" * 63_000,
        ("k" * 60 + "=v ") * 900,           # many long keys
    ],
)
def test_pathological_input_is_linear_time(payload):
    start = time.perf_counter()
    try:
        DeclarativeParser(KV).parse(payload)
    except ParserError:
        pass
    assert time.perf_counter() - start < 1.0


def test_config_rejects_unknown_keys_and_bad_values():
    with pytest.raises(ValidationError):
        DeclarativeParserConfig(strategy="regex")
    with pytest.raises(ValidationError):
        DeclarativeParserConfig.model_validate({"strategy": "kv", "pattern": "(a+)+$"})
    with pytest.raises(ValidationError):
        DeclarativeParserConfig(strategy="delimited", delimiter="|", columns=["ok", "bad name!"])
    with pytest.raises(ValidationError):
        DeclarativeParserConfig(strategy="delimited", delimiter="x", columns=["a", "b"])
    with pytest.raises(ValidationError):
        DeclarativeParserConfig(strategy="delimited", delimiter="|", columns=["a", "a"])


def _adapter(**overrides):
    data = {
        "id": "acme", "vendor": "ACME", "product": "FW", "format": "kv",
        "match": {"format": "kv", "field": "vendor", "equals": "ACME"},
        "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
        "parser": {"strategy": "kv"},
    }
    data.update(overrides)
    return AdapterMapping.model_validate(data)


def test_adapter_format_must_agree_with_parser():
    with pytest.raises(ValidationError):
        _adapter(format="delimited")
    with pytest.raises(ValidationError):
        AdapterMapping.model_validate({**_adapter().model_dump(), "parser": None})


def test_registry_find_declarative_requires_parse_and_match():
    registry = AdapterRegistry([_adapter()])
    found = registry.find_declarative("vendor=ACME a=1 b=2")
    assert found is not None and found[0].id == "acme" and found[2]["a"] == "1"
    assert registry.find_declarative("vendor=OTHER a=1 b=2") is None  # parses, but identity does not match
    assert registry.find_declarative("just some text") is None
    assert AdapterRegistry([]).find_declarative("vendor=ACME a=1 b=2") is None
