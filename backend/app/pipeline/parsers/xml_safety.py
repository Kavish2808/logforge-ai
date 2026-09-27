"""Hardened XML parsing primitives (Phase 8). Isolated so every security
property can be tested on its own.

Uses only the standard-library expat binding (no dependency) with a policy of
**no DTDs at all**. Log events never need a DTD, so rejecting DOCTYPE and
every other declaration closes the whole class of DTD attacks by construction:

- XXE / external entities / external DTD: any <!DOCTYPE, <!ENTITY, ... is
  rejected before parsing, and again by expat handlers during parsing; param
  entity parsing is disabled; the external-entity handler refuses; nothing is
  ever fetched, so no file or network access can happen.
- Entity expansion (billion laughs, quadratic blowup): impossible without
  entity declarations; undefined entity references are a parse error.
- Resource bounds: input size, nesting depth, element count, attributes per
  element and total attribute count are all capped; parsing is a single
  streaming pass that aborts as soon as a bound is exceeded.
- Encoding: the input is the already-decoded log string, encoded as strict
  UTF-8 and parsed as UTF-8 (a conflicting XML declaration cannot switch
  decoders); unencodable input (e.g. lone surrogates) is rejected.

Every failure is an XmlRejected with a short reason. Nothing else escapes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.parsers import expat

MAX_XML_BYTES = 256_000        # same order as the ingest API's per-log cap
MAX_DEPTH = 64
MAX_ELEMENTS = 5_000
MAX_ATTRIBUTES_PER_ELEMENT = 64
MAX_TOTAL_ATTRIBUTES = 20_000

_DECLARATION_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)", re.IGNORECASE)
_START_RE = re.compile(r"^(?:<\?xml[^>]{0,256}\?>\s*)?<[A-Za-z_]")


class XmlRejected(Exception):
    """The input is not acceptable XML under the hardened policy."""


@dataclass
class XmlNode:
    tag: str
    attrs: dict[str, str]
    children: list["XmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


def looks_like_xml(text: str) -> bool:
    """Cheap structural precheck (no parsing): an optional XML declaration,
    then an element start tag, and a closing '>' at the end."""
    return bool(_START_RE.match(text)) and text.endswith(">")


def _refuse(*_args) -> None:
    raise XmlRejected("DTD / entity declarations are not allowed")


def _refuse_external(*_args) -> int:
    raise XmlRejected("external entity references are not allowed")


def safe_parse(text: str) -> XmlNode:
    """Parse `text` into an XmlNode tree under the hardened policy."""
    if not isinstance(text, str):
        raise XmlRejected("input must be text")
    try:
        data = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise XmlRejected("input is not valid UTF-8 text") from exc
    if len(data) > MAX_XML_BYTES:
        raise XmlRejected(f"input exceeds {MAX_XML_BYTES} bytes")
    if _DECLARATION_RE.search(text):
        raise XmlRejected("DTD / entity declarations are not allowed")

    parser = expat.ParserCreate(encoding="UTF-8")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.NotationDeclHandler = _refuse
    parser.ElementDeclHandler = _refuse
    parser.AttlistDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external
    parser.buffer_text = True

    stack: list[XmlNode] = []
    root: list[XmlNode] = []
    counters = {"elements": 0, "attributes": 0}

    def start(tag: str, attrs: dict[str, str]) -> None:
        counters["elements"] += 1
        counters["attributes"] += len(attrs)
        if counters["elements"] > MAX_ELEMENTS:
            raise XmlRejected(f"more than {MAX_ELEMENTS} elements")
        if len(stack) >= MAX_DEPTH:
            raise XmlRejected(f"nesting deeper than {MAX_DEPTH}")
        if len(attrs) > MAX_ATTRIBUTES_PER_ELEMENT:
            raise XmlRejected(f"more than {MAX_ATTRIBUTES_PER_ELEMENT} attributes on one element")
        if counters["attributes"] > MAX_TOTAL_ATTRIBUTES:
            raise XmlRejected(f"more than {MAX_TOTAL_ATTRIBUTES} attributes in total")
        node = XmlNode(tag, dict(attrs))
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_tag: str) -> None:
        stack.pop()

    def chars(data_: str) -> None:
        if stack:
            stack[-1].text_parts.append(data_)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except XmlRejected:
        raise
    except expat.ExpatError as exc:
        raise XmlRejected(f"malformed XML ({expat.ErrorString(exc.code)} at line {exc.lineno})") from exc
    except (RecursionError, MemoryError, ValueError, TypeError) as exc:  # defensive: never escape as a crash
        raise XmlRejected(f"XML could not be parsed safely ({type(exc).__name__})") from exc
    if len(root) != 1:
        raise XmlRejected("XML must have exactly one root element")
    return root[0]


def is_safe_xml(text: str) -> bool:
    try:
        safe_parse(text)
    except XmlRejected:
        return False
    return True
