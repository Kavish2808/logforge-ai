"""Parser for JSON-formatted log events (one JSON object per log)."""
import json

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError


class JSONParser(BaseParser):
    format_name = "json"

    def parse(self, raw_log: str) -> ParseResult:
        text = raw_log.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParserError(f"Invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ParserError("JSON log must be an object at the top level")

        return ParseResult(fields=data, format_detected=self.format_name, warnings=[])
