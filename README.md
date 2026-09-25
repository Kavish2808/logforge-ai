# LogForge AI

**Universal Adaptive Log Pre-processing Framework — MVP (Phases 0–4, hardened)**

LogForge AI takes raw security/application logs in whatever format they arrive
in, and turns them into a consistent, OCSF-aligned structured event —
without ever losing the original data, even when parsing fails.

```
Any Log → Detect → Parse → Normalize → Preserve → (Learn*) → (Analyze*)
```
<sub>*Learn (LLM-assisted onboarding) and Analyze (drift detection, dashboard analytics) are later phases — see "Not yet implemented" below.</sub>

## What it does today

- Accepts a raw log line (syslog, JSON, or CEF) via the ingestion API.
- Detects its format deterministically — no network call, no LLM, no guesswork.
- Parses it into a flat set of fields using a format-specific parser.
- Selects a vendor mapping (YAML-defined "adapter") — either a vendor-specific
  one (Cisco ASA, Fortinet, Palo Alto Networks) or a generic per-format
  fallback — and normalizes the fields into an OCSF-aligned shape.
- Preserves **every** field the adapter didn't explicitly map, under `extensions`.
- Hashes the exact raw payload (SHA-256) and assigns it a unique, time-sortable
  event ID (ULID) — before any parsing is attempted, so traceability holds
  even for events that fail to parse.
- Computes a structural fingerprint (the field set/order the parser saw) —
  stored today, used for drift detection in a later phase.
- Persists the complete record (raw + normalized + extensions + metadata) in
  Postgres and returns it.
- Never returns a misleading result: the response `status` is always one of
  `SUCCESS` / `PARTIAL` / `FAILED`, and a raw log is **never** dropped —
  even on an unexpected internal error.

## Architecture

```
backend/app/
  api/routes/     FastAPI routes — thin, delegate to services, no business logic
  services/       ingestion_service.py: orchestration, batch isolation, defensive guards
  pipeline/        The deterministic core — no network dependency, ever:
    detector/       format detection (syslog | json | cef | unknown)
    parsers/         format-specific parsing (base.py defines the interface;
                      registry.py maps format -> parser so a new parser never
                      requires touching the orchestrator)
    normalizer/       adapter-driven OCSF mapping, type/timestamp coercion,
                      unknown-field preservation
    fingerprint/      structural fingerprinting
    hashing.py        SHA-256 of the raw payload
    orchestrator.py   wires the above into one deterministic pipeline
  adapters/        YAML vendor mappings + loader/registry (validated, no code)
  schema/          Pydantic models: OCSF universal event, adapter mapping,
                    API request/response, error envelope
  db/              SQLAlchemy models + repository layer (no ORM logic leaks
                    into services)
frontend/src/      Minimal React shell (dashboard UI is a later phase)
```

**Why the pipeline has no network dependency:** everything from `detector/`
through `hashing.py` operates purely on in-process data (parsers and adapter
YAML are loaded from disk at startup). The only phase-6 feature that will
ever call an external service is LLM-assisted onboarding for unrecognized
vendors — and it is explicitly isolated from this ingestion path so an LLM
outage can never affect normal log processing.

## Supported formats

| Format | RFCs/spec covered |
|---|---|
| Syslog | RFC 3164 (legacy BSD) and RFC 5424 |
| JSON | any single JSON object per log line |
| CEF | ArcSight Common Event Format (`CEF:Version\|...\|Extension`) |

An unrecognized format is not an error condition — it's persisted with
`status: FAILED`, `format_detected: unknown`, and the raw log fully intact.

## Vendor mappings (adapters)

Adapters are plain YAML under `backend/app/adapters/mappings/` — adding or
changing one never requires touching Python code. Shipped adapters:

| Adapter | Format | Selected when |
|---|---|---|
| `cisco_asa` | syslog | `app_name` contains `%ASA-` |
| `fortinet` | syslog | `app_name` equals `FORTIGATE` (message body is key=value, auto-flattened by the syslog parser — a generic capability, not vendor-specific code) |
| `paloalto_cef` | cef | `device_vendor` equals `Palo Alto Networks` |
| `syslog_generic` | syslog | fallback when no vendor mapping matches |
| `json_generic` | json | fallback when no vendor mapping matches |
| `cef_generic` | cef | fallback when no vendor mapping matches |

Each adapter declares: OCSF class/category, a `field_map` (source field →
OCSF-aligned target, with optional type coercion), severity mapping, and
which fields carry the event's action/severity/timestamp/product version.
Loading validates every mapping (Pydantic), rejects **duplicate adapter
IDs**, and rejects malformed YAML with a clear error — nothing fails silently
at load time.

If two mappings in the same adapter ever target the same OCSF field, the
first one wins deterministically and the conflicting source field is
preserved under `extensions` with a warning — it is never silently
overwritten.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness/readiness (checks DB connectivity) |
| POST | `/api/v1/ingest` | Ingest one raw log |
| POST | `/api/v1/ingest/batch` | Ingest up to 1000 logs; each is isolated — one bad log never fails the batch |
| GET | `/api/v1/events` | List/filter events (`vendor`, `status`, `format`, `adapter_id`, `limit`, `offset`) |
| GET | `/api/v1/events/{event_id}` | Fetch one event |
| POST | `/api/v1/events/{event_id}/reprocess` | Re-run the pipeline against the stored raw log using current adapters |

Interactive docs: `http://localhost:8000/docs`.

## Running it

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`, health at `/health`)
- Frontend: http://localhost:5173
- Postgres: localhost:5432

Alembic migrations run automatically on backend startup (`alembic upgrade head`).
Docker is the authoritative environment — no local Python toolchain is
required for the standard workflow (backend deps compile against
`python:3.11-slim` inside the image).

## Environment variables

See `.env.example`. All configuration is via environment variables loaded
through `pydantic-settings`; there is **no authentication** in this MVP by
design (not in the PRD's MVP scope). Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (set automatically in docker-compose) |
| `CORS_ORIGINS` | Comma-separated allowed origins for the frontend |
| `DEBUG` | Logging verbosity only — never enables stack-trace leakage (see Error handling) |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `DRIFT_SIMILARITY_THRESHOLD` | Reserved for Phase 5/6, unused by any code path today |

## Example: ingesting a log

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "<166>Jan 18 12:05:00 ciscoasa %ASA-6-302013: Built outbound TCP connection 123456789 for outside:203.0.113.10/443 to inside:10.0.0.20/52345"}'
```

### Example normalized event (abridged)

```json
{
  "event_id": "01M3BQH0180RSKTHTQ3RPEM2BS",
  "raw_hash": "e78048f54dd571992c5b04dbb659d35d0033f0aa2ccb8ab78fc0749d8c1a68a1",
  "format_detected": "syslog",
  "vendor": "Cisco",
  "product": "ASA",
  "adapter_id": "cisco_asa",
  "ocsf_class_name": "Network Activity",
  "event_type": "Firewall",
  "severity": "Informational",
  "status": "SUCCESS",
  "network": {},
  "extensions": {"rfc": "3164", "facility": 20},
  "normalized_event": {
    "device_hostname": "ciscoasa",
    "cisco_message_id": "%ASA-6-302013",
    "event_message": "Built outbound TCP connection 123456789 ..."
  },
  "structural_fingerprint": {"field_count": 8, "signature": "b9eb98fe..."},
  "warnings": [],
  "error_message": null
}
```

Full curl walkthrough: `docs/curl-examples.md`. Narrated demo: `scripts/demo.sh`
(logs in `demo/logs/`).

## Error handling

Every non-2xx response uses one consistent envelope:

```json
{"error": {"code": "VALIDATION_ERROR", "message": "...", "fields": {"raw_log": "..."}}}
```

- `422 VALIDATION_ERROR` — request failed Pydantic validation (missing/empty
  `raw_log`, over the 256 KB single-log / 1000-item batch limits, or a
  `raw_log` containing a NUL byte, which Postgres cannot store).
- `404 NOT_FOUND` — unknown `event_id`.
- `500 INTERNAL_ERROR` — an unexpected failure. The client only ever sees a
  generic message; full details (stack trace included) are logged
  server-side only. Debug mode (`DEBUG=true`) controls log verbosity, not
  whether stack traces reach the client — they never do, in any mode.

At the pipeline level (not HTTP errors), a per-event `error_message` string
and `warnings` list explain exactly what happened — e.g. an unparseable
timestamp, a mapping conflict, or a value too long for its column having
been truncated. Any of these downgrades `status` to `PARTIAL`; a total parse
failure is `FAILED`. The raw log and its hash are persisted in both cases.

## Testing

```bash
docker compose up -d db
docker compose run --rm backend pytest -v
```

Tests are organized as:
- `tests/unit/` — parsers, detector, normalizer, adapters, hashing, IDs,
  fingerprinting, all with zero DB/network dependency.
- `tests/integration/` — full FastAPI app against a real Postgres instance:
  ingestion, persistence, reprocessing, batch isolation, failure
  preservation, error envelopes, request-size limits.

Integration tests run against a dedicated `<db name>_test` database
(created automatically on first run), never the database a running
`docker compose up` stack is using — running the suite is always safe to
do alongside a live demo without touching its data.

## Current MVP limitations

- **No authentication** — not in MVP scope per the PRD.
- **No dashboard UI** — the React app is a shell only (health check, ready
  for the dashboard phase).
- **Vendor mappings don't regex-parse free-text message bodies.** They map
  already-extracted fields declaratively. The one generic exception: the
  syslog parser auto-flattens a `key=value` message payload (any vendor,
  not just Fortinet) so YAML mappings can reference those keys directly.
- **RFC3164 syslog has no year field** — missing years default to the
  current year at parse time (a known limitation of the format itself, not
  of this implementation).
- **No deduplication** — the same raw log ingested twice creates two events
  (the `raw_hash` index exists for future dedup/traceability lookups, but
  no dedup logic runs today).
- **Batch ingestion commits per-item**, not in one transaction — this is
  intentional (a crash mid-batch must not lose already-processed events),
  not an oversight.

## Not yet implemented (by design — future phases)

- **Phase 5** — Drift detection (SimHash/MinHash-based, baseline comparison,
  drift alerts, `UNDER_REVIEW` auto-marking).
- **Phase 6** — LLM-assisted unknown-vendor onboarding (sample collection,
  pattern generation, sandbox testing, human-approval workflow). The
  `llm/` provider interface is intentionally not built yet; `LLM_PROVIDER`/
  `ANTHROPIC_API_KEY` config exists but is unused.
- Dashboard UI with processing metrics.
- Kafka, OpenSearch, MinIO, dead-letter queue, horizontal workers — all
  explicitly deferred per the PRD.
- PII tokenization — no code path for it exists yet; the PRD's standard
  flow diagram includes a tokenization step for external destinations, but
  it is out of scope until a later phase.
