"""Applies an AdapterMapping to a parser's flat field dict, producing an
OCSF-aligned normalization result: typed nested groups (network/user/process),
top-level OCSF classification, and everything left over preserved as extensions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.pipeline.normalizer.extension_handler import compute_extensions
from app.pipeline.normalizer.field_normalizer import coerce_type, parse_timestamp
from app.schema.adapter import AdapterMapping
from app.schema.ocsf import NetworkInfo, ProcessInfo, UserInfo


@dataclass
class NormalizationResult:
    vendor: str | None
    product: str | None
    product_version: str | None
    ocsf_class_uid: int | None
    ocsf_class_name: str | None
    ocsf_category_uid: int | None
    ocsf_category_name: str | None
    event_type: str | None
    event_action: str | None
    severity: str | None
    network: dict[str, Any]
    user: dict[str, Any]
    process: dict[str, Any]
    normalized_event: dict[str, Any]
    extensions: dict[str, Any]
    event_timestamp: datetime | None
    warnings: list[str] = field(default_factory=list)


# Typed OCSF groups, validated with exactly the field types the read model
# (UniversalEvent) uses, so a stored event can always be read back.
_GROUP_MODELS = {"network": NetworkInfo, "user": UserInfo, "process": ProcessInfo}
_GROUP_ADAPTERS: dict[str, TypeAdapter] = {
    f"{group}.{name}": TypeAdapter(info.annotation)
    for group, model in _GROUP_MODELS.items()
    for name, info in model.model_fields.items()
}


def _typed_group_error(target: str, value: Any) -> str | None:
    adapter = _GROUP_ADAPTERS.get(target)
    if adapter is None:
        return None
    try:
        adapter.validate_python(value)
    except ValidationError as exc:
        return exc.errors()[0]["msg"]
    return None


def _short(value: Any, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def normalize(parsed_fields: dict[str, Any], adapter: AdapterMapping) -> NormalizationResult:
    warnings: list[str] = []
    network: dict[str, Any] = {}
    user: dict[str, Any] = {}
    process: dict[str, Any] = {}
    flat: dict[str, Any] = {}
    consumed_keys: set[str] = set()

    used_targets: set[str] = set()

    for source_key, entry in adapter.resolved_field_map().items():
        if source_key not in parsed_fields:
            continue

        target = entry.target
        if target in used_targets:
            # Two source fields map to the same OCSF target (e.g. an
            # adapter mistake). Deterministically keep whichever came
            # first in the YAML (dict order == file order) and leave the
            # loser in extensions rather than silently overwriting data.
            warnings.append(
                f"Mapping conflict: '{source_key}' also targets '{target}', which was already set "
                f"by an earlier field; '{source_key}' was preserved in extensions instead."
            )
            continue

        value, coerce_warning = coerce_type(parsed_fields[source_key], entry.type)
        type_error = _typed_group_error(target, value)
        if type_error is not None:
            # The value does not fit the typed OCSF group field (e.g. "-" for
            # network.dst_port). Placing it there would produce an event that
            # cannot be read back, so it is not mapped: the source field stays
            # in extensions with its original value and the event is PARTIAL.
            warnings.append(
                f"{source_key}: value {_short(parsed_fields[source_key])} is not a valid {target} ({type_error}); "
                f"'{source_key}' was preserved in extensions instead."
            )
            continue

        consumed_keys.add(source_key)
        used_targets.add(target)
        if coerce_warning:
            warnings.append(f"{source_key}: {coerce_warning}")

        if "." in target:
            group, _, attr = target.partition(".")
            if group == "network":
                network[attr] = value
            elif group == "user":
                user[attr] = value
            elif group == "process":
                process[attr] = value
            else:
                flat[target] = value
        else:
            flat[target] = value

    for special_key in (
        adapter.event_action_field,
        adapter.severity_field,
        adapter.timestamp_field,
        adapter.product_version_field,
    ):
        if special_key:
            consumed_keys.add(special_key)

    event_action: str | None = None
    if adapter.event_action_field:
        raw = parsed_fields.get(adapter.event_action_field)
        event_action = str(raw) if raw is not None else None

    severity: str | None = None
    if adapter.severity_field:
        raw_severity = parsed_fields.get(adapter.severity_field)
        if raw_severity is not None:
            severity = adapter.severity_map.get(str(raw_severity), str(raw_severity))

    product_version: str | None = None
    if adapter.product_version_field:
        raw_pv = parsed_fields.get(adapter.product_version_field)
        product_version = str(raw_pv) if raw_pv is not None else None

    event_timestamp: datetime | None = None
    if adapter.timestamp_field:
        raw_ts = parsed_fields.get(adapter.timestamp_field)
        event_timestamp, ts_warning = parse_timestamp(raw_ts, adapter.timestamp_format)
        if ts_warning:
            warnings.append(ts_warning)

    for key, value in adapter.static_fields.items():
        flat[key] = value

    extensions = compute_extensions(parsed_fields, consumed_keys)

    normalized_event: dict[str, Any] = {
        "vendor": adapter.vendor,
        "product": adapter.product,
        "event_type": adapter.event_type,
        "event_action": event_action,
        "severity": severity,
        "network": network,
        "user": user,
        "process": process,
        **flat,
    }

    return NormalizationResult(
        vendor=adapter.vendor,
        product=adapter.product,
        product_version=product_version,
        ocsf_class_uid=adapter.ocsf.class_uid,
        ocsf_class_name=adapter.ocsf.class_name,
        ocsf_category_uid=adapter.ocsf.category_uid,
        ocsf_category_name=adapter.ocsf.category_name,
        event_type=adapter.event_type,
        event_action=event_action,
        severity=severity,
        network=network,
        user=user,
        process=process,
        normalized_event=normalized_event,
        extensions=extensions,
        event_timestamp=event_timestamp,
        warnings=warnings,
    )
