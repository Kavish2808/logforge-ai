"""Loads and validates YAML vendor mapping files into an in-memory registry.

Adapters are plain YAML files under `mappings/` (generic per-format
fallbacks plus vendor-specific overrides under `mappings/vendors/`).
Adding or editing a vendor mapping never requires touching Python code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.declarative import DeclarativeParser
from app.schema.adapter import AdapterMapping, MatchRule

_MAPPINGS_DIR = Path(__file__).parent / "mappings"


class AdapterRegistry:
    def __init__(self, adapters: list[AdapterMapping]):
        self._adapters = adapters

    def all(self) -> list[AdapterMapping]:
        return list(self._adapters)

    def get(self, adapter_id: str) -> AdapterMapping | None:
        for adapter in self._adapters:
            if adapter.id == adapter_id:
                return adapter
        return None

    def find_for(self, format_name: str, parsed_fields: dict) -> AdapterMapping | None:
        """Select the best adapter for a parsed event.

        Vendor-specific adapters (those with a `match` rule) are tried
        first; the first format-matching adapter with no `match` rule is
        used as the generic fallback.
        """
        fallback: AdapterMapping | None = None
        for adapter in self._adapters:
            if adapter.format != format_name:
                continue
            if adapter.match is None:
                if fallback is None:
                    fallback = adapter
                continue
            if _match_rule_applies(adapter.match, parsed_fields):
                return adapter
        return fallback

    def find_declarative(self, raw_log: str) -> tuple[AdapterMapping, DeclarativeParser, dict] | None:
        """For logs the built-in detector cannot classify: the first adapter
        with a declarative parser that both parses the log and whose match
        rule applies to the parsed fields. Adapters are tried in registry
        order (shipped YAML first, then onboarded adapters by approval
        order). Returns (adapter, parser, parsed_fields) or None."""
        for adapter in self._adapters:
            if adapter.parser is None or adapter.match is None:
                continue
            parser = DeclarativeParser(adapter.parser)
            try:
                fields = parser.parse(raw_log).fields
            except ParserError:
                continue
            if _match_rule_applies(adapter.match, fields):
                return adapter, parser, fields
        return None


def _match_rule_applies(rule: MatchRule, parsed_fields: dict) -> bool:
    if rule.field is None:
        return False
    value = parsed_fields.get(rule.field)
    if value is None:
        return False
    value_str = str(value)
    if rule.equals is not None:
        return value_str == rule.equals
    if rule.contains is not None:
        return rule.contains in value_str
    return False


def load_adapters(base_dir: Path = _MAPPINGS_DIR) -> AdapterRegistry:
    adapters: list[AdapterMapping] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(base_dir.rglob("*.yaml")):
        raw = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Adapter mapping in {path} must be a YAML mapping (object) at the top level")

        try:
            adapter = AdapterMapping.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Invalid adapter mapping in {path}: {exc}") from exc

        if adapter.id in seen_ids:
            raise ValueError(
                f"Duplicate adapter id '{adapter.id}' in {path} (already defined in {seen_ids[adapter.id]})"
            )
        seen_ids[adapter.id] = path
        adapters.append(adapter)

    return AdapterRegistry(adapters)


@lru_cache
def get_adapter_registry() -> AdapterRegistry:
    return load_adapters()
