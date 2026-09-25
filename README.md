# LogForge AI

**Universal Adaptive Log Pre-processing Framework — MVP (Phases 0–4 hardened + Phase 5 drift detection)**

LogForge AI takes raw security/application logs in whatever format they arrive
in, and turns them into a consistent, OCSF-aligned structured event —
without ever losing the original data, even when parsing fails.

```
Any Log → Detect → Parse → Normalize → Preserve → Detect Drift → (Learn*) → (Analyze*)
```
<sub>*Learn (LLM-assisted onboarding) and Analyze (dashboard analytics) are later phases — see "Not yet implemented" below.</sub>

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
- Computes a structural fingerprint (the field set, order and value types
  the parser saw).
- Detects **structural drift** for known vendor sources: compares each
  event's fingerprint against that source's baseline and marks events whose
  structure changed as `UNDER_REVIEW` for mandatory human review — without
  discarding or altering anything (see "Drift detection" below).
- Persists the complete record (raw + normalized + extensions + metadata) in
  Postgres and returns it.
- Never returns a misleading result: the response `status` is always one of
  `SUCCESS` / `PARTIAL` / `FAILED` / `UNDER_REVIEW`, and a raw log is
  **never** dropped — even on an unexpected internal error.

## Architecture

```
backend/app/
  api/routes/     FastAPI routes — thin, delegate to services, no business logic
  services/       ingestion_service.py: orchestration, batch isolation, defensive guards
                  drift_service.py: baseline lookup/bootstrap, drift decision, human review
  pipeline/        The deterministic core — no network dependency, ever:
    detector/       format detection (syslog | json | cef | unknown)
    parsers/         format-specific parsing (base.py defines the interface;
                      registry.py maps format -> parser so a new parser never
                      requires touching the orchestrator)
    normalizer/       adapter-driven OCSF mapping, type/timestamp coercion,
                      unknown-field preservation
    fingerprint/      structural fingerprinting
    drift/            pure structural drift comparator (Phase 5)
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
| POST | `/api/v1/events/{event_id}/reprocess` | Re-run the pipeline against the stored raw log using current adapters (drift is re-evaluated too) |
| GET | `/api/v1/drift/baselines` | List per-source structural baselines (origin, version, accepted variants, under-review count) |
| GET | `/api/v1/drift/baselines/{source_key}` | Fetch one baseline (`source_key` = vendor `adapter_id`) |
| POST | `/api/v1/events/{event_id}/drift/accept` | Human review of a drifted event (`add_variant`, `replace_baseline` or `acknowledge`) |

`GET /drift/baselines/{source_key}` and the accept response include the
source's structural `history`; every event's full drift record (including
the human-readable `explanation`) is on `GET /events/{event_id}` under
`processing_metadata.drift`.

The drift review queue is `GET /api/v1/events?status=UNDER_REVIEW` (optionally
`&adapter_id=...`). `POST /ingest/batch` responses also include
`under_review_count`.

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
| `DRIFT_ENABLED` | `true` (default) enables drift detection; `false` restores Phase 0-4 ingestion behavior exactly |
| `DRIFT_SIMILARITY_THRESHOLD` | `0.0`–`1.0` (default `0.85`); vendor events scoring below it vs. their baseline become `UNDER_REVIEW` |
| `DRIFT_CRITICAL_FIELDS` | Comma-separated OCSF targets (default `event_action,severity,network.src_ip,network.dst_ip,network.src_port,network.dst_port`) whose removal or type change always forces review |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY` | Reserved for Phase 6, unused by any code path today |

## Drift detection (Phase 5)

```
Known vendor source → structural fingerprint → compare against baseline + approved variants
→ similarity + critical-field rules → configurable threshold → NORMAL or DRIFT
→ classification · severity · explanation · re-onboarding recommendation
```

Drift detection is an explainable, deterministic structural engine — no
SimHash/MinHash, ML, embeddings, LLM or network call anywhere in it. The
comparator makes the NORMAL/DRIFT decision; everything else (change types,
severity, explanation, recommendations) is derived from the same structural
evidence and is reproducible bit-for-bit.

**What is evaluated.** Events matched by a *vendor-specific* adapter
(`cisco_asa`, `fortinet`, `paloalto_cef`); the adapter id is the
`source_key`. Generic fallback adapters (`*_generic`) never get a baseline
of their own — they aggregate unrelated sources — but their events are
checked for **possible format drift** (below). `FAILED` events are never
evaluated.

**Baselines.** The first structure observed for a source is stored
automatically as a **provisional, auto-bootstrapped baseline**
(`origin: "auto_bootstrap"`). It is never changed automatically after
that — it can only be extended or replaced through human review (`origin`
becomes `"human_review"` when replaced). Baselines live in the
`source_baselines` table.

**Scoring.** Deterministic, standard library only (no SimHash/MinHash/ML).
A weighted sum of four structural signals, compared against the baseline
reference and every accepted variant (best match wins):

| Signal | Measure | Weight |
|---|---|---|
| Field presence/absence | Jaccard similarity of field sets | 0.55 |
| Field ordering | sequence similarity of field order | 0.20 |
| Field count | min/max ratio | 0.10 |
| Value types (schema) | share of shared, non-null fields whose JSON type is unchanged | 0.15 |

The weights are **MVP heuristics**, defined as named constants in
`app/pipeline/drift/comparator.py`; only the threshold is configuration.
A format change against the baseline always counts as drift. At the
default threshold `0.85`, for an 8-field source: one added field scores
≈0.92 (NORMAL, but the difference is still reported), two added fields
≈0.85 → DRIFT, a fully reversed field order ≈0.83 → DRIFT.

**Critical fields.** `DRIFT_CRITICAL_FIELDS` lists OCSF targets; each is
resolved to the vendor's raw field name through its own adapter YAML
(`field_map`, `event_action_field`, `severity_field`) — e.g.
`network.src_ip` is `src` for Palo Alto and `srcip` for Fortinet. An adapter
may add vendor-specific raw names with an optional `critical_fields:` list.
If a critical field present in the baseline is **removed or changes type**,
the event drifts regardless of similarity (removing the source IP from a
17-field Palo Alto event still scores ≈0.96), the field is reported in
`critical_field_changes`, severity is at least `HIGH`, and
`reonboarding_required` is `true`.

**Classification.** Every applicable change type is listed in
`change_types`: `FIELD_ADDITION`, `FIELD_REMOVAL`, `FIELD_TYPE_CHANGE`,
`FIELD_ORDER_CHANGE`, `FORMAT_DRIFT`, plus `MULTIPLE_STRUCTURAL_CHANGE` when
two or more occur together. `decision_reasons` says why the event drifted
(`SIMILARITY_BELOW_THRESHOLD`, `CRITICAL_FIELD_CHANGED`, `FORMAT_CHANGED`,
`ADAPTER_FALLBACK`).

**Severity** (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`) is a deterministic points
formula — deliberately *not* the similarity score — defined as named
constants in `app/pipeline/drift/analysis.py`:

| Factor | Points |
|---|---|
| each added field | 1 |
| each removed field | 2 |
| each type-changed field | 4 |
| field order changed | 1 |
| structural magnitude | ⌊10 × (1 − field-set Jaccard)⌋ |
| each critical field removed / type-changed | 6 |
| adapter fallback (possible format drift) | 9 |
| log format changed | 15 |

`LOW` < 4 ≤ `MEDIUM` < 9 ≤ `HIGH` < 15 ≤ `CRITICAL`; any critical-field
change is at least `HIGH`, a format change is always `CRITICAL`. Every
non-zero contribution is returned in `severity_factors`.

**Re-onboarding recommendation.** `recommended_actions` lists what a
reviewer should look at, most important first: `REVIEW_CRITICAL_FIELD_CHANGE`,
`REVIEW_FORMAT_DRIFT`, `REVIEW_REMOVED_FIELDS`, `REVIEW_TYPE_CHANGES`,
`REVIEW_ADDED_FIELDS`, `REVIEW_FIELD_ORDER`. Metadata only — nothing acts on it.

**Approved variants.** Every structure is compared against the reference
baseline *and* every human-approved variant; the event reports
`matched: "reference"` or `matched: "variant:N"`. A structure matching an
approved variant is `NORMAL` with no change report, and leaves the baseline
and its history untouched. The same structure is never added twice (neither
as a duplicate variant nor as a copy of the reference).

**Structural history.** Each baseline version is one row in
`source_baseline_history` — no event copies, just the delta and the event
that triggered it — so `GET /drift/baselines/{source_key}` answers "what
changed in this source over time?":

```
v1 BASELINE_CREATED   first observed structure (auto_bootstrap)
v2 VARIANT_ADDED      +cn1 +cs2 +cs2Label +deviceExternalId +rt  -cs1 -cs1Label -suser
v3 BASELINE_REPLACED  +app +rule +sessionid  ...
```

**Possible format drift (adapter fallback).** If a vendor's format changes
so much that its adapter's `match` rule stops firing, the event falls
through to a generic adapter. Such events are checked, deterministically,
for evidence of a **previously known** vendor source (one with a baseline):

| Evidence | Meaning |
|---|---|
| `MATCH_FIELD_NEAR_MISS` | the vendor's match field is present and contains the match value case-insensitively, but the exact rule no longer matches (e.g. `FORTIGATE` → `FortiGate`) |
| `VENDOR_IDENTITY_FIELD_IN_OTHER_FORMAT` | the vendor's identity field appears in a different log format (e.g. Palo Alto `device_vendor` in a JSON log) |
| `VENDOR_SIGNATURE_IN_RAW` | the raw log contains the vendor's match value (≥ 4 characters) |

With any evidence the event gets `drift.status: POSSIBLE_FORMAT_DRIFT` —
never a definite claim, since generic adapters legitimately receive other
data — recording the suspected `source_key`, the `current_adapter`, the
`evidence`, similarity to the known baseline, severity (`HIGH`, or
`CRITICAL` if the log format changed), `REVIEW_FORMAT_DRIFT` and
`reonboarding_required: true`. The event goes to `UNDER_REVIEW` and counts
toward that source's `under_review_count`. Without evidence (or without a
known vendor baseline) generic events are untouched. Adapters are never
changed automatically.

**When drift is detected** the event is persisted in full — raw log, hash,
normalized event, extensions and the new fingerprint are all preserved —
with `status: UNDER_REVIEW`. The decision is recorded at
`processing_metadata.drift` (example: a Palo Alto CEF event whose
extension keys changed):

```json
{
  "status": "DRIFT",
  "source_key": "paloalto_cef",
  "baseline_version": 1,
  "baseline_origin": "auto_bootstrap",
  "matched": "reference",
  "similarity": 0.7451,
  "threshold": 0.85,
  "components": {"field_set": 0.6364, "field_order": 0.7778, "field_count": 0.8947, "field_types": 1.0},
  "differences": {
    "added_fields": ["cn1", "cs2", "cs2Label", "deviceExternalId", "rt"],
    "removed_fields": ["cs1", "cs1Label", "suser"],
    "field_count": {"baseline": 17, "current": 19},
    "order_changed": false,
    "type_changes": {}
  },
  "change_types": ["FIELD_ADDITION", "FIELD_REMOVAL", "MULTIPLE_STRUCTURAL_CHANGE"],
  "decision_reasons": ["SIMILARITY_BELOW_THRESHOLD"],
  "severity": "HIGH",
  "severity_score": 14,
  "severity_factors": {"added_fields": 5, "removed_fields": 6, "structural_magnitude": 3},
  "critical_field_changes": [],
  "recommended_actions": ["REVIEW_REMOVED_FIELDS", "REVIEW_ADDED_FIELDS"],
  "original_status": "SUCCESS",
  "reonboarding_required": true,
  "recommended_action": "The structure of known source 'paloalto_cef' changed. ...",
  "explanation": "DRIFT DETECTED\nSource: paloalto_cef\nBaseline Version: 1 (auto_bootstrap)\n..."
}
```

The `explanation` is rendered from the structured fields on every read
(`app/pipeline/drift/explain.py`, a fixed template — no LLM):

```
DRIFT DETECTED
Source: paloalto_cef
Baseline Version: 1 (auto_bootstrap)
Matched: reference
Similarity: 0.7451
Threshold: 0.85
Severity: HIGH (score 14)
Change types: FIELD_ADDITION, FIELD_REMOVAL, MULTIPLE_STRUCTURAL_CHANGE
Decision: SIMILARITY_BELOW_THRESHOLD

Changes:
+ cn1
+ cs2
+ cs2Label
+ deviceExternalId
+ rt
- cs1
- cs1Label
- suser

Recommendation:
REVIEW_REMOVED_FIELDS, REVIEW_ADDED_FIELDS
Human review required.
```

Type changes render as `~ dpt type changed string → integer`, reordering as
`↕ field order changed`, format changes as `⇄ format changed cef → json`,
and critical fields as `! critical field removed: src (network.src_ip)`.

`drift.status` is one of `BASELINE_CREATED` (this event bootstrapped the
baseline), `NORMAL`, `DRIFT`, `POSSIBLE_FORMAT_DRIFT`, or `ERROR` (drift
evaluation itself failed — the event is still persisted with the status the
pipeline gave it; drift detection can never turn an event into `FAILED`).

**Human review (mandatory).** Nothing is re-onboarded automatically: no
parser or adapter is ever modified or deployed by the system. A reviewer
works the queue (`GET /events?status=UNDER_REVIEW`), updates the adapter
YAML by hand if needed, then calls `POST /events/{event_id}/drift/accept`:

- `add_variant` — the new structure becomes an additional accepted
  structure for the source (the auto-bootstrapped reference is kept);
- `replace_baseline` — the new structure becomes the reference and prior
  variants are cleared (`origin: "human_review"`);
- `acknowledge` — mark the event reviewed without changing the baseline
  (the only mode for `POSSIBLE_FORMAT_DRIFT`, whose structure belongs to a
  generic adapter and can't join the vendor's baseline).

Each mode restores the event's pre-drift status, records the review in
`drift.review`, and — for baseline changes — appends to the source history. Other queued events with the same
structure are resolved by reprocessing them (`POST /events/{id}/reprocess`),
which re-evaluates drift and carries any prior review forward.

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
  "structural_fingerprint": {"field_count": 8, "signature": "b9eb98fe...", "field_types": {"...": "string"}},
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
  fingerprinting, the drift comparator, all with zero DB/network dependency.
- `tests/integration/` — full FastAPI app against a real Postgres instance:
  ingestion, persistence, reprocessing, batch isolation, failure
  preservation, error envelopes, request-size limits, drift detection and
  human review.

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
- **Drift detection is top-level and structural only.** Changes inside
  nested JSON objects surface only as a type change of the parent key;
  value-level changes (e.g. new enum values) are not drift.
- **Format drift detection is evidence-based, not exhaustive.** An event
  that fell back to a generic adapter is flagged `POSSIBLE_FORMAT_DRIFT` only
  if it still carries the vendor's match value (near-miss, other format, or
  in the raw text). A change that removes every trace of the vendor's
  identity is not linked back to the vendor, and a log that merely *mentions*
  a vendor's signature can be flagged (hence "possible"). Logs that no longer
  parse at all stay `FAILED`, exactly as in Phase 0-4, and are not
  annotated.
- **Critical fields are checked at the parser (top) level**, resolved
  through the adapter's current YAML; a critical field that is only nested
  inside another field cannot be tracked individually.
- **Severity weights and bands are MVP heuristics** (named constants), not
  tuned against production data; the threshold is the only runtime knob.
- **Reprocessing re-evaluates drift from scratch** (the prior human review is
  carried forward): an *acknowledged* drift whose structure was never
  accepted into the baseline returns to `UNDER_REVIEW` on reprocess.
- **Naturally variable sources can raise false positives** (e.g. Fortinet
  log subtypes with different key sets, CEF events with optional
  extensions). Reviewers accept these as variants (capped at 50 per source).
- **The first observed structure becomes the provisional baseline** — if it
  was unrepresentative, a reviewer replaces it via `replace_baseline`.

## Not yet implemented (by design — future phases)

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
