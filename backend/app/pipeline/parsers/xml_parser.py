"""Native XML parser (Phase 8): hardened parse -> deterministic flat fields.

No vendor schema is assumed. The element tree is flattened structurally:

- ``xml_root``            the root element name
- ``@attr``               attributes of the root element
- ``A.B.C``               text of leaf element C under B under A (root excluded)
- ``A.B.@attr``           attributes of element B
- ``A.B.#text``           non-whitespace text directly inside an element that
                          also has child elements (mixed content)
- ``Tag[0]``, ``Tag[1]``  siblings sharing a tag name are indexed by position
                          (in document order) whenever there is more than one

Leaf and mixed text is stripped of surrounding whitespace (pretty-printing);
the untouched raw event is preserved by the pipeline regardless. Every value
is a string, so nothing is coerced here; the adapter/normalizer decide.
"""
from __future__ import annotations

from collections import Counter

from app.pipeline.parsers.base import BaseParser, ParseResult, ParserError
from app.pipeline.parsers.xml_safety import XmlNode, XmlRejected, safe_parse


def _flatten(node: XmlNode, prefix: str, fields: dict[str, str]) -> None:
    for name, value in node.attrs.items():
        fields[f"{prefix}@{name}"] = value
    if not node.children:
        if prefix:
            fields[prefix.rstrip(".")] = node.text.strip()
        elif node.text.strip():
            fields["#text"] = node.text.strip()
        return
    mixed = node.text.strip()
    if mixed:
        fields[f"{prefix}#text"] = mixed
    counts = Counter(child.tag for child in node.children)
    seen: Counter = Counter()
    for child in node.children:
        if counts[child.tag] > 1:
            key = f"{child.tag}[{seen[child.tag]}]"
            seen[child.tag] += 1
        else:
            key = child.tag
        _flatten(child, f"{prefix}{key}.", fields)


class XMLParser(BaseParser):
    format_name = "xml"

    def parse(self, raw_log: str) -> ParseResult:
        try:
            root = safe_parse(raw_log.strip())
        except XmlRejected as exc:
            raise ParserError(f"XML rejected: {exc}") from exc
        flat: dict[str, str] = {}
        _flatten(root, "", flat)
        warnings: list[str] = []
        root_key = "xml_root"
        if root_key in flat:  # an element literally named xml_root: never overwrite it
            root_key = "xml_root#"
            warnings.append("An element named 'xml_root' exists; the root element name is under 'xml_root#'.")
        fields = {root_key: root.tag, **flat}
        return ParseResult(fields=fields, format_detected=self.format_name, warnings=warnings)
