"""Base interface every format parser must implement.

New parsers are added by subclassing BaseParser and registering an
instance in `registry.py` — the pipeline orchestrator never needs to
change to support a new format.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ParserError(Exception):
    """Raised when a parser cannot parse the given raw log at all."""


@dataclass
class ParseResult:
    """Flat set of fields extracted from a raw log, prior to OCSF normalization."""

    fields: dict[str, Any]
    format_detected: str
    warnings: list[str] = field(default_factory=list)


class BaseParser(ABC):
    format_name: str

    @abstractmethod
    def parse(self, raw_log: str) -> ParseResult:
        """Parse a raw log string into a flat dict of extracted fields.

        Must raise ParserError if the log cannot be parsed by this parser.
        Must never silently swallow malformed input.
        """
        raise NotImplementedError
