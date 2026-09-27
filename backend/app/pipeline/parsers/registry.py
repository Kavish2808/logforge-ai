"""Parser registry: maps a detected format name to its parser instance.

To add a new parser, implement BaseParser and call `register_parser()`
below — nothing in the pipeline orchestrator needs to change.
"""
from app.pipeline.parsers.base import BaseParser
from app.pipeline.parsers.cef_parser import CEFParser
from app.pipeline.parsers.json_parser import JSONParser
from app.pipeline.parsers.leef_parser import LEEFParser
from app.pipeline.parsers.syslog_parser import SyslogParser
from app.pipeline.parsers.xml_parser import XMLParser

_REGISTRY: dict[str, BaseParser] = {}


def register_parser(parser: BaseParser) -> None:
    _REGISTRY[parser.format_name] = parser


def get_parser(format_name: str) -> BaseParser | None:
    return _REGISTRY.get(format_name)


register_parser(SyslogParser())
register_parser(JSONParser())
register_parser(CEFParser())
# Phase 8: native formats reached only via secondary detection.
register_parser(LEEFParser())
register_parser(XMLParser())
