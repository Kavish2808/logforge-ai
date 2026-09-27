# LogForge export contract — `logforge.export.v1`

The machine-readable JSON Schema (2020-12) is served by the API at
`GET /api/v1/export/schema`, generated from `backend/app/export/schema.py`. This page
documents the same contract for people. The API output is authoritative.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/export/events` | Export with query-string filters (same names as `/api/v1/views/events`) |
| `POST` | `/api/v1/export/events` | Same selection as a JSON body (use for long `event_ids` lists) |
| `GET` | `/api/v1/export/schema` | Published schema + semantics |
| `GET` | `/api/v1/export/logs` | Recent export activity (who, filters, rows, completion) |

**Output selector:** `output=ndjson` (default) or `output=json`. The name `format` is kept
for the **log-format filter** so that the filter names match the Views API exactly.

**Filters:** `start`, `end` (on `received_at`), `status` (repeatable), `source`, `vendor`,
`product`, `format`, `adapter_id`, `adapter_version`, `drift_status`, `drift_severity`,
`severity`, `category`, `search`, `event_id` (repeatable on GET; `event_ids` in the POST body).

**Bounds:** `limit` is capped at `EXPORT_MAX_EVENTS` (NDJSON, default 50,000) or
`EXPORT_JSON_MAX_EVENTS` (JSON, default 5,000). The applied bound is returned in
`X-LogForge-Max-Events`. Rows are read in keyset batches of `EXPORT_BATCH_SIZE` (default 500)
from a READ ONLY transaction and streamed, so memory use does not grow with the export size.

**Pagination:** newest first by `(received_at, event_id)`. When `has_more` is true, pass
`next_cursor` back as `cursor`. The cursor is a keyset position, so pages stay stable while
new events arrive.

## Framing

- **NDJSON** (`application/x-ndjson`): one event record per line, then **one trailer line**:
  `{"schema_version","record_type":"trailer","export_id","count","has_more","next_cursor","complete":true,"generated_at"}`.
  An NDJSON export is complete only if it ends with that trailer. A missing trailer means the
  stream was cut off.
- **JSON** (`application/json`): `{"schema_version","export_id","generated_at","filters","items":[...],"count","has_more","next_cursor"}`.

## Event record

**Required fields:**
`schema_version` (`"logforge.export.v1"`), `record_type` (`"event"`), `event_id`, `received_at`,
`status` (`SUCCESS|PARTIAL|FAILED|UNDER_REVIEW`), `format`, `extensions`, `extension_storage`,
`field_accounting`, `raw`, `integrity`, `revision`.

**Optional / nullable fields:**
`processed_at`, `event_timestamp`, `source_key`, `vendor`, `product`, `product_version`,
`adapter {id, version, source}`, `ocsf {class_uid, class_name, category_uid, category_name}`,
`event_type`, `event_action`, `severity`, `severity_id`, `network`, `user`, `process`,
`normalized` (the OCSF-aligned normalized representation; `null` for FAILED events),
`drift {status, severity, source_key}`, `warnings[]`, `error_message`.

### Extensions

`extensions` always holds **every** parsed field that was not mapped to a typed field, whether
it is stored inline on the event row or was spilled to overflow storage. Nothing is truncated.
`extension_storage` records where the fields live:
`{mode: INLINE|SPILLED, inline_field_count, overflow_field_count, overflow_sha256}`.

### Field accounting

`{parsed_count, mapped_count, preserved_count, preserved_inline, preserved_overflow, method}`, where
`parsed_count = mapped_count + preserved_count`. Mapped means a parsed field that is not
preserved in extensions, i.e. the adapter consumed it. For the per-field accounting against
the exact adapter version, use `GET /api/v1/views/events/{id}/lineage`.

### Raw and integrity

- `raw {sha256, byte_size, encoding: "utf-8", payload, vault}`. `payload` is only filled with
  `include_raw=true`. `vault {backend, object_key, status, tier}` locates the cold,
  content-addressed copy.
- `integrity {algorithm: "SHA-256", raw_sha256, merkle}`. `raw_sha256` is the SHA-256 of the raw
  payload's UTF-8 bytes. `merkle` is `null` until the event is sealed. Once sealed it holds
  `{batch_id, batch_seq, leaf_index, leaf_hash, root_hash, chain_hash}`.
  `GET /api/v1/integrity/events/{event_id}` returns the inclusion proof and checks the anchor.

### Revision semantics

`event_id` is stable forever, and the raw payload and `raw_sha256` never change.
Reprocessing can change the normalized content, extensions and status.
`revision.revision_hash` is a SHA-256 over the canonical normalized content (status, adapter
version, normalized/typed groups, extensions, warnings). Consumers should **upsert by `event_id`**
and keep the record with the newest `revision.processed_at`, or detect changes by comparing
`revision_hash`.

### Compatibility

Within `v1`, fields are only ever added, never removed, renamed or re-typed. Consumers must
ignore unknown fields. A breaking change would ship as `logforge.export.v2` alongside v1.

## Not included

This is an export and integration contract. Direct forwarding to a SIEM (push delivery,
retries, acknowledgements) is **not** implemented.
