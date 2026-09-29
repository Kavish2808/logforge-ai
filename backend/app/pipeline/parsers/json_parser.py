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
            if isinstance(data, list):
                if len(data) == 1 and isinstance(data[0], dict):
                    data = data[0]
                elif data and all(isinstance(x, dict) for x in data):
                    data = {"items": data, "count": len(data), "message": f"Array of {len(data)} events"}
                else:
                    raise ParserError("JSON log must be an object at the top level")
            else:
                raise ParserError("JSON log must be an object at the top level")

        return ParseResult(fields=data, format_detected=self.format_name, warnings=[])
