"""Published, versioned output schema for exported events (JSON Schema 2020-12).

Versioning: SCHEMA_VERSION follows "logforge.export.v<MAJOR>". Within a major
version fields are only ever *added* (optional); a removal, rename or type
change is a new major version served side by side. Consumers must ignore
unknown fields.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "logforge.export.v1"
SCHEMA_ID = "https://logforge.ai/schemas/export/v1/event-record.json"

_str_or_null = {"type": ["string", "null"]}
_int_or_null = {"type": ["integer", "null"]}
_obj_or_null = {"type": ["object", "null"]}
_sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_sha256_or_null = {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}
_time = {"type": "string", "format": "date-time"}
_time_or_null = {"type": ["string", "null"], "format": "date-time"}

EVENT_RECORD: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "title": "LogForge exported event record",
    "type": "object",
    "required": ["schema_version", "record_type", "event_id", "received_at", "status", "format", "integrity",
                 "raw", "extensions", "extension_storage", "field_accounting", "revision"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "record_type": {"const": "event"},
        "event_id": {"type": "string", "description": "ULID; stable identity of the event across revisions."},
        "received_at": _time,
        "processed_at": _time_or_null,
        "event_timestamp": _time_or_null,
        "status": {"enum": ["SUCCESS", "PARTIAL", "FAILED", "UNDER_REVIEW"]},
        "format": {"type": "string"},
        "source_key": _str_or_null,
        "vendor": _str_or_null,
        "product": _str_or_null,
        "product_version": _str_or_null,
        "adapter": {"type": "object", "properties": {"id": _str_or_null, "version": _str_or_null,
                                                     "source": _str_or_null}},
        "ocsf": {"type": "object", "properties": {"class_uid": _int_or_null, "class_name": _str_or_null,
                                                  "category_uid": _int_or_null, "category_name": _str_or_null}},
        "event_type": _str_or_null,
        "event_action": _str_or_null,
        "severity": _str_or_null,
        "severity_id": _int_or_null,
        "network": _obj_or_null,
        "user": _obj_or_null,
        "process": _obj_or_null,
        "normalized": {**_obj_or_null, "description": "OCSF-aligned normalized representation (null for FAILED)."},
        "extensions": {"type": "object",
                       "description": "EVERY parsed field not mapped to a typed field, reassembled from inline "
                                      "and overflow storage. Keys are raw field names; values are as parsed."},
        "extension_storage": {
            "type": "object", "required": ["mode", "inline_field_count", "overflow_field_count"],
            "properties": {"mode": {"enum": ["INLINE", "SPILLED"]}, "inline_field_count": {"type": "integer"},
                           "overflow_field_count": {"type": "integer"}, "overflow_sha256": _sha256_or_null}},
        "field_accounting": {
            "type": "object", "required": ["parsed_count", "mapped_count", "preserved_count"],
            "properties": {"parsed_count": {"type": "integer"}, "mapped_count": {"type": "integer"},
                           "preserved_count": {"type": "integer"}, "preserved_inline": {"type": "integer"},
                           "preserved_overflow": {"type": "integer"}, "method": {"type": "string"}}},
        "drift": {"type": ["object", "null"], "properties": {"status": _str_or_null, "severity": _str_or_null,
                                                             "source_key": _str_or_null}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "error_message": _str_or_null,
        "raw": {
            "type": "object", "required": ["sha256", "byte_size", "encoding"],
            "properties": {
                "sha256": _sha256, "byte_size": {"type": "integer"}, "encoding": {"const": "utf-8"},
                "payload": {"type": ["string", "null"], "description": "Only when include_raw=true."},
                "vault": {"type": ["object", "null"], "properties": {
                    "backend": {"type": "string"}, "object_key": _str_or_null, "status": {"type": "string"},
                    "tier": {"type": "string"}}}}},
        "integrity": {
            "type": "object", "required": ["raw_sha256", "algorithm"],
            "properties": {
                "algorithm": {"const": "SHA-256"}, "raw_sha256": _sha256,
                "merkle": {"type": ["object", "null"], "description": "Null until the event is sealed into a batch.",
                           "properties": {"batch_id": {"type": "string"}, "batch_seq": {"type": "integer"},
                                          "leaf_index": {"type": "integer"}, "leaf_hash": _sha256,
                                          "root_hash": _sha256, "chain_hash": _sha256}}}},
        "revision": {
            "type": "object", "required": ["revision_hash"],
            "properties": {"revision_hash": _sha256, "pipeline_version": _str_or_null,
                           "processed_at": _time_or_null, "adapter_version": _str_or_null}},
    },
}

TRAILER_RECORD: dict[str, Any] = {
    "title": "NDJSON trailer (always the last line of a complete NDJSON export)",
    "type": "object",
    "required": ["schema_version", "record_type", "export_id", "count", "has_more", "next_cursor", "complete"],
    "properties": {"schema_version": {"const": SCHEMA_VERSION}, "record_type": {"const": "trailer"},
                   "export_id": {"type": "string"}, "count": {"type": "integer"}, "has_more": {"type": "boolean"},
                   "next_cursor": _str_or_null, "complete": {"const": True}, "generated_at": _time},
}

SEMANTICS: dict[str, Any] = {
    "identity": "event_id is stable forever. The raw payload and raw_sha256 never change.",
    "revisions": "Reprocessing may change normalized content, extensions and status. revision.revision_hash is "
                 "SHA-256 over the canonical normalized content; consumers should upsert by event_id and keep the "
                 "record with the newest revision.processed_at (or detect change by revision_hash).",
    "extensions": "extensions always contains the complete set of preserved fields, whether they are stored "
                  "inline or spilled to overflow storage; extension_storage says which.",
    "integrity": "raw_sha256 = SHA-256 of the raw payload's UTF-8 bytes. When sealed, integrity.merkle locates the "
                 "event's leaf in a chained, anchored Merkle batch; verify via GET /api/v1/integrity/events/{event_id}.",
    "ordering": "Events are exported newest first by (received_at, event_id). next_cursor resumes after the last "
                "exported event (keyset pagination; stable under concurrent inserts).",
    "completeness": "An NDJSON export is complete only if its last line is a trailer record with complete=true. "
                    "has_more=true means the bounded limit was reached; continue with next_cursor.",
    "compatibility": "Within v1, fields are only added. Consumers must ignore unknown fields.",
}


def published() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "event_record": EVENT_RECORD, "ndjson_trailer": TRAILER_RECORD,
            "semantics": SEMANTICS, "formats": {
                "selector": "`output` parameter (GET query / POST body); `format` stays the log-format filter",
                "ndjson": "application/x-ndjson: one event record per line, then one trailer line.",
                "json": "application/json: {schema_version, export_id, generated_at, filters, items[], count, "
                        "has_more, next_cursor}."}}
