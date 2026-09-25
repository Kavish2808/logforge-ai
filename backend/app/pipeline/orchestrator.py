"""The core, deterministic ingestion pipeline.

raw log -> detect format -> parse -> select adapter -> normalize (OCSF)
-> preserve unknown fields as extensions -> structural fingerprint

Logs the built-in detector cannot classify are offered to human-approved
declarative (onboarded) adapters before being marked FAILED.

This module makes no network calls and has no external dependency: it
operates purely on in-process registries (parsers, adapters) that are
loaded from local files at process startup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.adapters.loader import AdapterRegistry, get_adapter_registry
from app.pipeline.detector.format_detector import detect_format
from app.pipeline.fingerprint.structural import compute_fingerprint
from app.pipeline.hashing import sha256_hex
from app.pipeline.normalizer.ocsf_mapper import normalize
from app.pipeline.parsers.base import ParserError, ParseResult
from app.pipeline.parsers.registry import get_parser
from app.schema.ocsf import EventStatus, FormatType

PIPELINE_VERSION = "0.1.0"


@dataclass
class PipelineResult:
    raw_event: str
    raw_hash: str
    format_detected: str
    status: str
    received_at: datetime
    processed_at: datetime
    event_timestamp: datetime | None = None
    vendor: str | None = None
    product: str | None = None
    product_version: str | None = None
    adapter_id: str | None = None
    adapter_version: str | None = None
    ocsf_class_uid: int | None = None
    ocsf_class_name: str | None = None
    ocsf_category_uid: int | None = None
    ocsf_category_name: str | None = None
    event_type: str | None = None
    event_action: str | None = None
    severity: str | None = None
    severity_id: int | None = None
    network: dict[str, Any] = field(default_factory=dict)
    user: dict[str, Any] = field(default_factory=dict)
    process: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    normalized_event: dict[str, Any] | None = None
    processing_metadata: dict[str, Any] = field(default_factory=dict)
    structural_fingerprint: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None


def process(
    raw_log: str,
    *,
    received_at: datetime | None = None,
    adapter_registry: AdapterRegistry | None = None,
) -> PipelineResult:
    received_at = received_at or datetime.now(tz=timezone.utc)
    raw_hash = sha256_hex(raw_log)
    registry = adapter_registry or get_adapter_registry()

    format_detected = detect_format(raw_log)

    # Onboarded sources: a log the built-in detector cannot classify may
    # still match a human-approved declarative adapter. With no such adapter
    # registered this is always None and behavior is exactly as before.
    declarative = registry.find_declarative(raw_log) if format_detected == FormatType.UNKNOWN else None

    if format_detected == FormatType.UNKNOWN and declarative is None:
        return PipelineResult(
            raw_event=raw_log,
            raw_hash=raw_hash,
            format_detected=format_detected.value,
            status=EventStatus.FAILED.value,
            received_at=received_at,
            processed_at=datetime.now(tz=timezone.utc),
            processing_metadata={"pipeline_version": PIPELINE_VERSION, "parser": None},
            error_message="Could not detect a known log format (syslog/json/cef).",
        )

    if declarative is not None:
        declarative_adapter, parser, declarative_fields = declarative
        format_name = declarative_adapter.format
        parse_result = ParseResult(fields=declarative_fields, format_detected=format_name)
    else:
        declarative_adapter = None
        format_name = format_detected.value
        parser = get_parser(format_name)
        if parser is None:
            # Defensive: every FormatType other than UNKNOWN has a registered parser.
            return PipelineResult(
                raw_event=raw_log,
                raw_hash=raw_hash,
                format_detected=format_name,
                status=EventStatus.FAILED.value,
                received_at=received_at,
                processed_at=datetime.now(tz=timezone.utc),
                processing_metadata={"pipeline_version": PIPELINE_VERSION, "parser": None},
                error_message=f"No parser registered for format '{format_name}'.",
            )

        try:
            parse_result = parser.parse(raw_log)
        except ParserError as exc:
            return PipelineResult(
                raw_event=raw_log,
                raw_hash=raw_hash,
                format_detected=format_name,
                status=EventStatus.FAILED.value,
                received_at=received_at,
                processed_at=datetime.now(tz=timezone.utc),
                processing_metadata={"pipeline_version": PIPELINE_VERSION, "parser": parser.format_name},
                error_message=str(exc),
            )

    fingerprint = compute_fingerprint(parse_result.fields)
    warnings = list(parse_result.warnings)

    adapter = declarative_adapter or registry.find_for(format_name, parse_result.fields)
    if adapter is None:
        # No generic fallback is registered for this format (shouldn't happen
        # in practice — every shipped format has one) -> preserve everything
        # as extensions and flag for manual review rather than dropping data.
        return PipelineResult(
            raw_event=raw_log,
            raw_hash=raw_hash,
            format_detected=format_name,
            status=EventStatus.PARTIAL.value,
            received_at=received_at,
            processed_at=datetime.now(tz=timezone.utc),
            extensions=parse_result.fields,
            processing_metadata={
                "pipeline_version": PIPELINE_VERSION,
                "parser": parser.format_name,
                "adapter_id": None,
            },
            structural_fingerprint=fingerprint,
            warnings=warnings + ["No adapter mapping matched; all fields preserved as extensions."],
        )

    norm = normalize(parse_result.fields, adapter)
    warnings.extend(norm.warnings)

    status = EventStatus.SUCCESS.value if not warnings else EventStatus.PARTIAL.value

    return PipelineResult(
        raw_event=raw_log,
        raw_hash=raw_hash,
        format_detected=format_name,
        status=status,
        received_at=received_at,
        processed_at=datetime.now(tz=timezone.utc),
        event_timestamp=norm.event_timestamp,
        vendor=norm.vendor,
        product=norm.product,
        product_version=norm.product_version,
        adapter_id=adapter.id,
        adapter_version=adapter.version,
        ocsf_class_uid=norm.ocsf_class_uid,
        ocsf_class_name=norm.ocsf_class_name,
        ocsf_category_uid=norm.ocsf_category_uid,
        ocsf_category_name=norm.ocsf_category_name,
        event_type=norm.event_type,
        event_action=norm.event_action,
        severity=norm.severity,
        network=norm.network,
        user=norm.user,
        process=norm.process,
        extensions=norm.extensions,
        normalized_event=norm.normalized_event,
        processing_metadata={
            "pipeline_version": PIPELINE_VERSION,
            "parser": parser.format_name,
            "adapter_id": adapter.id,
            "adapter_source": adapter.source,
        },
        structural_fingerprint=fingerprint,
        warnings=warnings,
    )
