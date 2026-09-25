"""Declarative (configuration-only) parser for onboarded log formats.

Interprets a DeclarativeParserConfig — never code, never a regex. Both
strategies are linear-time scans with hard size limits, so neither
untrusted log content nor an AI-suggested configuration can cause
catastrophic backtracking or unbounded work.
"""
from __future__ import annotations

import csv

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError
from app.schema.adapter import FIELD_NAME_RE, DeclarativeParserConfig

MAX_DECLARATIVE_LINE_LENGTH = 64_000
MAX_KV_PAIRS = 512


class DeclarativeParser(BaseParser):
    def __init__(self, config: DeclarativeParserConfig):
        self.config = config
        self.format_name = config.strategy

    def parse(self, raw_log: str) -> ParseResult:
        text = raw_log.strip()
        if not text:
            raise ParserError("Empty log")
        if len(text) > MAX_DECLARATIVE_LINE_LENGTH:
            raise ParserError(f"Log exceeds {MAX_DECLARATIVE_LINE_LENGTH} characters for declarative parsing")
        if "\n" in text or "\r" in text:
            raise ParserError("Declarative parsers accept single-line records only")
        if self.config.strategy == "delimited":
            fields = self._parse_delimited(text)
        else:
            fields = self._parse_kv(text)
        return ParseResult(fields=fields, format_detected=self.format_name, warnings=[])

    def _parse_delimited(self, text: str) -> dict[str, str]:
        assert self.config.delimiter is not None
        try:
            values = next(csv.reader([text], delimiter=self.config.delimiter, strict=True))
        except (csv.Error, StopIteration) as exc:
            raise ParserError(f"Malformed delimited record: {exc}") from exc
        columns = self.config.columns
        if len(values) != len(columns):
            raise ParserError(f"Expected {len(columns)} delimited columns, got {len(values)}")
        return {name: value.strip() for name, value in zip(columns, values)}

    def _parse_kv(self, text: str) -> dict[str, str]:
        pairs = split_kv_pairs(text, self.config.pair_separator, self.config.kv_separator)
        if len(pairs) < self.config.min_pairs:
            raise ParserError(f"Expected at least {self.config.min_pairs} key-value pairs, got {len(pairs)}")
        return pairs


def split_kv_pairs(text: str, pair_separator: str, kv_separator: str) -> dict[str, str]:
    """Linear scan of `key<kv_separator>value` tokens. Tokens are split on
    `pair_separator` (or any whitespace) outside double quotes; tokens that
    are not a valid key-value pair are ignored. First occurrence of a key
    wins. Raises ParserError on an unterminated quote."""
    tokens: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and in_quotes and i + 1 < n:
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif not in_quotes and (ch.isspace() if pair_separator == "whitespace" else ch == pair_separator):
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
        i += 1
    if in_quotes:
        raise ParserError("Unterminated quoted value")
    if buf:
        tokens.append("".join(buf))

    pairs: dict[str, str] = {}
    for token in tokens:
        token = token.strip()
        key, sep, value = token.partition(kv_separator)
        if not sep or not FIELD_NAME_RE.match(key):
            continue
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if key not in pairs:
            pairs[key] = value
            if len(pairs) > MAX_KV_PAIRS:
                raise ParserError(f"More than {MAX_KV_PAIRS} key-value pairs")
    return pairs
