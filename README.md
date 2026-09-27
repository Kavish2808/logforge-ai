# LogForge AI

**Universal Adaptive Log Pre-processing Framework — MVP**
Phases 0–4 (ingestion core), Phase 3 (adaptive unknown-vendor onboarding),
Phase 5 (drift detection), Phase 6 (continuous adaptive learning), a read-only
operational intelligence API, a React operational console, and a reproducible
Demo Mode.

> **A log parser that learns:** LogForge can learn a new vendor, detect when
> that vendor changes, explain the change, learn an approved structural
> evolution, safely validate the new parser version, require human approval,
> activate the new version, preserve previous versions, and continue
> detecting future changes.

LogForge AI takes raw security/application logs in whatever format they arrive
in, and turns them into a consistent, OCSF-aligned structured event —
without ever losing the original data, even when parsing fails.

```
Any Log → Detect → Parse → Normalize → Preserve → Detect Drift → Inspect (console)
Unknown source → Learn from samples → Validate → Human approval → Versioned adapter → auto-parsed
```

> **Phases.** Phase 3 — *adaptive onboarding*: "I have never seen this source — learn it."
> Phase 5 — *drift detection*: "I know this source — something about it changed."
> Phase 6 — *continuous adaptive learning*: "this approved change has become part of what I know."
>
> ```
> New vendor → Phase 3 learn → human approval → adapter v1 → production logs
> → Phase 5 drift → explain → human accepts → Phase 6 learn the change
> → sandbox (new + historical logs) → human approval → activate v2
> → future logs use v2 → Phase 5 keeps monitoring against the learned structure ↺
> ```

**Status at a glance** (details in [Status: current, deferred, future scale architecture](#status-current-deferred-future-scale-architecture)):

| Current | Not implemented yet |
|---|---|
| Ingestion of syslog (RFC 3164/5424), JSON and CEF; raw + SHA-256 preservation; OCSF-aligned normalization; field accounting | Removing raw payloads from PostgreSQL (cold-only tier); S3/MinIO vault backend |
| Unknown-vendor onboarding (offline analyzer by default; Claude provider optional) | LEEF and XML formats |
| Structural drift detection with mandatory human review | SIEM forwarding, data-lake integration |
| Continuous adaptive learning with versioning and rollback | SSO/OAuth/FIDO2, production deployment profile |
| Read-only operational intelligence API + React console | Queue/worker scale-out architecture (billion-events/day is a target, not a measured result) |
| Reproducible Demo Mode (CLI and console) | Live Claude inference has **not** been verified (no API key was available) |
| Phase 7: extension spill, cold raw vault, Merkle evidence chain + local WORM-style anchors, streaming export (`logforge.export.v1`), RBAC + maker-checker + production gate + login lockout, review SLA, confidence ledger, hash-chained audit, alert bus | External WORM / object-lock anchoring; verification against the hosted Slack/Teams services (the adapters are tested over real local HTTP/SMTP servers) |

## What it does today

- **Ingests** a raw log line (syslog, JSON, or CEF) via `POST /api/v1/ingest`,
  or up to 1000 per request via `/ingest/batch` (each item isolated).
- **Detects the format** deterministically — no network call, no LLM, no guesswork.
- **Parses** it into a flat set of fields using a format-specific parser.
- **Selects a vendor mapping** (a YAML "adapter") — Cisco ASA, Fortinet, Palo
  Alto Networks, a generic per-format fallback, or a human-approved onboarded
  adapter — and **normalizes** the fields into an OCSF-aligned shape.
- **Preserves every field** the adapter didn't map, under `extensions`.
- **Hashes the exact raw payload (SHA-256)** and assigns a unique,
  time-sortable event ID (ULID) *before* any parsing is attempted, so
  traceability holds even for events that fail to parse.
- **Fingerprints the structure** (field set, order, value types).
- **Detects structural drift** for known sources and holds drifted events as
  `UNDER_REVIEW` for mandatory human review (Phase 5).
- **Learns unknown vendors** from samples, with a sandbox and human approval
  before anything is active (Phase 3).
- **Learns approved changes** of onboarded sources as new, versioned adapter
  versions — validated against new and historical logs, activated only by a
  human, and rollback-able (Phase 6).
- **Explains every event** through a read-only lineage and field-accounting
  API and the operational console.
- **Persists** the complete record (raw + normalized + extensions + metadata)
  in Postgres. The response `status` is always one of `SUCCESS` / `PARTIAL` /
  `FAILED` / `UNDER_REVIEW`, and a raw log is **never** dropped — even on an
  unexpected internal error.

## Running it (Docker)

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL | Notes |
|---|---|---|
| Backend API | http://localhost:8000 | OpenAPI docs at `/docs`, health at `/health` (checks DB connectivity) |
| Console | http://localhost:5173 | React operational console |
| Postgres | localhost:5432 | `postgres:16-alpine`, data in the named volume `logforge_pgdata` |

Alembic migrations run automatically on backend startup (`alembic upgrade head`).
Docker is the authoritative environment — no local Python or Node toolchain is
required (the backend image is `python:3.11-slim`, the frontend image
`node:20-alpine`). The compose file is a **development** setup: the backend
runs `uvicorn --reload` and the frontend runs the Vite dev server; there is no
production deployment profile yet. The db and backend services have
healthchecks; the frontend service does not.

> On Windows bind mounts the Vite dev server may not pick up file edits; run
> `docker compose restart frontend` after changing frontend source.

## Architecture

```
backend/app/
  api/routes/     FastAPI routes — thin, delegate to services
                    ingest, events, drift, onboarding, learning  (Phases 0–6)
                    views   read-only operational intelligence API
                    demo    Demo Mode fixtures, status and scoped reset
  services/       ingestion_service.py  orchestration, batch isolation, defensive guards
                  drift_service.py      baseline lookup/bootstrap, drift decision, human review
                  onboarding_service.py Phase 3 sessions, sandbox, approval, runtime registry
                  learning_service.py   Phase 6 sessions, validation, approval, activation
                  views_service.py      read-only lineage, field accounting, source intelligence
                  demo_service.py       Demo Mode progress status + ownership-verified reset
  pipeline/       The deterministic core — no network dependency, ever:
    detector/       format detection (syslog | json | cef | unknown)
    parsers/        syslog, JSON, CEF + declarative kv / delimited parsers
                     (registry.py maps format -> parser)
    normalizer/     adapter-driven OCSF mapping, type/timestamp coercion,
                     unknown-field preservation
    fingerprint/    structural fingerprinting
    drift/          pure structural drift comparator, analysis, explanation (Phase 5)
    hashing.py      SHA-256 of the raw payload
    orchestrator.py wires the above into one deterministic pipeline
  adapters/       YAML vendor mappings + loader/registry (validated, no code)
  onboarding/     Phase 3: sample analysis, suggestion providers (Claude / offline),
                   proposal schema + evidence review, sandbox, explanation
  learning/       Phase 6: learning delta, deterministic learning engine,
                   optional LLM assistant, learning sandbox, report
  demo/           Demo Mode: deterministic fixtures + HTTP-only orchestrator (python -m app.demo)
  schema/         Pydantic models: OCSF universal event, adapter mapping, API models, errors
  db/             SQLAlchemy models + repositories
backend/alembic/  migrations 0001–0006
frontend/src/
  api/            typed API client (one function per endpoint)
  lib/            hash router, fetch hook (loading/error/retry), formatting
  components/     shared UI (badges, cards, tables, filter bar, lineage/timeline pieces)
  pages/          Overview, EventExplorer, EventForensics, Sources, DriftQueue,
                   Onboarding, AdapterEvolution, Learning, Export, Demo
scripts/          demo.sh (Phase 0–4 ingestion walkthrough), demo_mode.sh (Demo Mode)
```

**Why the pipeline has no network dependency:** everything from `detector/`
through `hashing.py` operates purely on in-process data; shipped adapter YAML
is loaded from disk and approved onboarded adapters from Postgres. The only
features that can call an external service are the *optional* Claude
suggestion provider (Phase 3) and learning assistant (Phase 6). Both are
isolated from ingestion, so an LLM outage — or no network at all — can never
affect log processing.

## Supported formats

| Format | Coverage |
|---|---|
| Syslog | RFC 3164 (legacy BSD) and RFC 5424; a `key=value` message body is auto-flattened |
| JSON | any single JSON object per log line |
| CEF | ArcSight Common Event Format (`CEF:Version\|...\|Extension`) |
| key=value, delimited | only through an **onboarded** adapter (configuration-only parsers, no regex) |

An unrecognized format is not an error condition — it's persisted with
`status: FAILED`, `format_detected: unknown`, and the raw log fully intact.
**LEEF and XML are not supported yet.**

## Vendor mappings (adapters)

Shipped adapters are plain YAML under `backend/app/adapters/mappings/` — adding
or changing one never requires touching Python code:

| Adapter | Format | Selected when |
|---|---|---|
| `cisco_asa` | syslog | `app_name` contains `%ASA-` |
| `fortinet` | syslog | `app_name` equals `FORTIGATE` (message body is key=value, auto-flattened by the syslog parser — a generic capability, not vendor-specific code) |
| `paloalto_cef` | cef | `device_vendor` equals `Palo Alto Networks` |
| `syslog_generic` | syslog | fallback when no vendor mapping matches |
| `json_generic` | json | fallback when no vendor mapping matches |
| `cef_generic` | cef | fallback when no vendor mapping matches |

**Onboarded adapters** (Phase 3) are stored as immutable, versioned rows in
Postgres and join the runtime registry only while a human-approved version is
`ACTIVE`; Phase 6 can add further versions (see below).

Each adapter declares: OCSF class/category, a `field_map` (source field →
OCSF-aligned target, with optional type coercion), severity mapping, and
which fields carry the event's action/severity/timestamp/product version.
Loading validates every mapping (Pydantic), rejects **duplicate adapter
IDs**, and rejects malformed YAML with a clear error — nothing fails silently
at load time. If two mappings in the same adapter target the same OCSF field,
the first one wins deterministically and the conflicting source field is
preserved under `extensions` with a warning — it is never silently overwritten.

## Data guarantees

- **Raw preservation.** The exact raw log is stored before parsing and is
  never modified — also for `FAILED` events and on internal errors.
- **SHA-256 integrity.** `raw_hash` is the SHA-256 of the raw payload. The
  lineage API recomputes it from the stored raw log on every request and
  reports whether it still matches.
- **OCSF-aligned normalization.** Events carry an OCSF class and category,
  typed `network` / `user` / `process` groups and a `normalized_event`.
  The shape is *aligned with* OCSF; it is not validated against the full OCSF
  schema.
- **Unknown-field preservation.** Every parsed field that no mapping claims is
  kept verbatim under `extensions`.
- **Field accounting.** For every parsed field the lineage API reports whether
  it was `MAPPED` (and to which target), `PRESERVED` (and where), or
  `UNACCOUNTED`. The verdict `nothing_silently_discarded` is true only when no
  field is unaccounted **and** the recomputed SHA-256 matches; the `basis` list
  states why.
- **Failure and partial handling.** A value that fails type validation for a
  typed group field is *not converted*: it is kept under `extensions` with its
  original value, a warning is recorded, and the event is `PARTIAL`. A total
  parse failure is `FAILED` with the raw log and hash persisted. One ingest
  always produces exactly one event row.

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
| GET | `/api/v1/drift/baselines/{source_key}` | Fetch one baseline (`source_key` = adapter id) |
| POST | `/api/v1/events/{event_id}/drift/accept` | Human review of a drifted event (`add_variant`, `replace_baseline` or `acknowledge`) |
| POST | `/api/v1/onboarding/sessions` | Start onboarding an unknown source from raw `samples` and/or existing `event_ids` |
| GET | `/api/v1/onboarding/sessions` · `/sessions/{id}` | List sessions · full session (samples, evidence, proposal, validation, activation, explanation) |
| POST | `/api/v1/onboarding/sessions/{id}/suggest` | Ask the suggestion engine (`auto` / `anthropic` / `offline`) for a proposal; it is validated immediately |
| PUT | `/api/v1/onboarding/sessions/{id}/proposal` | Submit a human-written/edited proposal (same validation) |
| POST | `/api/v1/onboarding/sessions/{id}/approve` · `/reject` | Human decision; only a PASSED proposal version can be approved (optional `adapter_id`) |
| GET | `/api/v1/onboarding/adapters` · `/adapters/{adapter_id}` | Versioned onboarded adapters |
| POST | `/api/v1/onboarding/adapters/{adapter_id}/rollback` | Withdraw the active version; the previous version becomes active again |
| POST | `/api/v1/events/{event_id}/learning/propose` | Phase 6: learn a human-accepted drift of an onboarded adapter (explicit action) |
| GET | `/api/v1/learning/sessions` · `/sessions/{id}` | List learning sessions (`source_key`, `status`) · full session with evidence, delta, validation, report |
| POST | `/api/v1/learning/sessions/{id}/validate` | Re-run the learning sandbox |
| PUT | `/api/v1/learning/sessions/{id}/proposal` | Submit a human-edited learning delta (same gates) |
| POST | `/api/v1/learning/sessions/{id}/approve` · `/request-review` · `/reject` | Human decisions (`approve` accepts `activate: true` for APPROVE & ACTIVATE) |
| POST | `/api/v1/learning/sessions/{id}/activate` · `/rollback` | Explicit, idempotent activation of the new version · rollback to the previous version |
| GET | `/api/v1/views/events` | Read-only event search with cursor pagination (see below) |
| GET | `/api/v1/views/summary` | Counts, distributions and an hourly/daily status trend |
| GET | `/api/v1/views/filters` | Distinct values for every filter |
| GET | `/api/v1/views/events/{event_id}/lineage` | Lineage chain, integrity re-check and field accounting of one event |
| GET | `/api/v1/views/sources` · `/sources/{source_key}` | Source intelligence (list · detail) |
| GET | `/api/v1/views/sources/{source_key}/timeline` | Chronological evolution of one source |
| GET | `/api/v1/demo/fixtures` · `/demo/status` | Demo Mode fixtures · read-only progress status |
| POST | `/api/v1/demo/reset` | Remove demo-owned rows only (see Demo Mode) |

`GET /drift/baselines/{source_key}` and the accept response include the
source's structural `history`; every event's full drift record (including
the human-readable `explanation`) is on `GET /events/{event_id}` under
`processing_metadata.drift`. The drift review queue is
`GET /api/v1/events?status=UNDER_REVIEW` (optionally `&adapter_id=...`);
`POST /ingest/batch` responses also include `under_review_count`.

Interactive docs: `http://localhost:8000/docs`.

## Operational intelligence API (`/api/v1/views`)

A read-only layer over the stored events and the Phase 3/5/6 records. Every
request runs in a `SET TRANSACTION READ ONLY` database transaction: this layer
cannot write, and it never parses, normalizes, learns or activates anything.

- **`/views/events`** — filters: `start`, `end`, `status` (repeatable),
  `source`, `vendor`, `product`, `format`, `adapter_id`, `adapter_version`,
  `drift_status` (including `NONE`), `drift_severity`, `severity`, `category`,
  and `search` (3–200 characters: a 26-character event id, a 64-character
  SHA-256, or otherwise a substring of the raw log). Keyset (cursor)
  pagination on `(received_at, event_id)` with an opaque cursor; `limit` ≤ 200;
  `include_total=true` adds the matching count.
- **`/views/summary`** — totals, counts by status / format / vendor / adapter /
  drift status / drift severity, adapter and session counts, and a status
  trend bucketed by `hour` or `day` (default `day`). Every number is a
  database count.
- **`/views/events/{id}/lineage`** — ten stages (raw event, format detection,
  parser, adapter @ version, normalization, field accounting, warnings, drift
  decision, baseline, learning history) with an outcome per stage, the
  SHA-256 re-check, and the field accounting described under
  [Data guarantees](#data-guarantees).
- **`/views/sources`**, **`/views/sources/{key}`** — per source: kind (shipped
  vendor, shipped generic, onboarded), event counts by status, partial rate,
  formats, adapter versions seen, active version, baseline and its history,
  drift counts, pending reviews, learning sessions, recent drift.
- **`/views/sources/{key}/timeline`** — onboarding, adapter activations and
  rollbacks, baseline changes, drift detections and reviews, and learning
  decisions in time order, each with links to the underlying records.

## Operational console (React)

A single-page React 18 + TypeScript console (Vite, no router dependency — hash
routes) that renders only API data: loading, empty and error states with
retry everywhere, no hard-coded metrics. Dark navigation, dense light
workspace; `SUCCESS` / `PARTIAL` / `FAILED` / `UNDER_REVIEW` are green / amber /
red / purple, and human-decision boundaries are drawn as dashed purple boxes.

| Route | Page | What it shows / does |
|---|---|---|
| `#/overview` | Overview | Totals and status percentages from `/views/summary`, events-over-time trend (hour/day), status and drift distributions, drift queue preview, top sources, recent events and learning activity |
| `#/events` | Event Explorer | Every Views filter, 400 ms debounced search, reset, cursor paging (newer/older), total count; a row opens Forensics |
| `#/events/{id}` | Event Forensics | The "NOTHING SILENTLY DISCARDED" verdict and its basis, RAW → PARSED → NORMALIZED → PRESERVED → VERIFIED strip, the ten-stage lineage, field accounting, and Raw / Normalized / Extensions / Warnings / Hash tabs. A value that failed a type check is shown as "not converted, not mapped, preserved verbatim" |
| `#/sources` · `#/sources/{key}` | Sources | Source list; detail with baseline + history, adapter versions, observed formats/versions and recent drift |
| `#/drift` | Drift Queue | Awaiting-review and all-drift tabs; per event: similarity vs threshold, changed fields, critical fields, recommendation, baseline comparison, explanation; **Accept as variant / Replace baseline / Acknowledge** only after an explicit confirm; "Propose learning" for accepted drift of onboarded sources |
| `#/onboarding` · `#/onboarding/{id}` | Onboarding | Session list and new-session form (pasted samples and/or stored FAILED unknown-format events); 7-step view: samples, analysis, suggestion (labelled with the provider that actually produced it), mapping review, sandbox validation, human approval (reject requires a reason), activation |
| `#/evolution` · `#/evolution/{key}` | Adapter Evolution | Version lane with a confirmed rollback, and the source timeline |
| `#/learning` · `#/learning/{id}` | Learning | Session list with status filter; detail with drift evidence, mapping diff, proposed mappings with evidence and confidence, sandbox validation, state-dependent human actions (validate, approve, approve & activate, activate, request review, reject, rollback), history and report |
| `#/export` | Export | Views filters, NDJSON / JSON output, max-events bound, optional raw payloads, streaming download through `/export/events`, "next page" via cursor, recent export activity |
| `#/integrity` | Integrity | Merkle chain (verify / seal now / batch list with anchors), per-event integrity check and byte-exact raw recovery from the cold vault, hot/cold distribution, extension overflow with onboarding evidence |
| `#/alerts` | Alerts | Open / acknowledged alerts with severity, read/unread, acknowledgement, delivery outcome per channel, channel configuration status |
| `#/audit` | Audit Log | Hash-chained audit records (actor, role, action, object, decision, evidence, hashes) with chain verification that flags broken records |
| `#/governance` | Governance | Sign-in / first-admin bootstrap, users and roles (SOC_ADMIN), review SLA queue, SLA and alert configuration, the published RBAC policy |
| `#/demo` | Demo | Demo Mode (below) |

The console is desktop-first; it remains usable down to ~768 px (the
navigation becomes a top bar and wide data tables scroll inside their cards).

## Demo Mode

One deterministic, repeatable end-to-end demonstration of the whole lifecycle
using the **real public API only** — no fabricated data, no shortcuts around
human approval.

```
unknown vendor → 12 samples + the failed log (13) → offline analysis → sandbox
→ HUMAN APPROVAL → adapter v1 → logs parse → drift (dst_port → dst_port_number, + policy_id, + zone)
→ HUMAN ACCEPTS DRIFT → learning proposal → sandbox regression (new + historical)
→ HUMAN APPROVAL → HUMAN ACTIVATES v2 → new and old structures both parse
→ HUMAN ROLLBACK → v1 active again, v2 preserved
```

**Fixtures** (`backend/app/demo/fixtures.py`) describe one fictional firewall,
`LogForgeDemo EdgeFirewall`: 32 fixed raw logs (no clock, no randomness), so
every run produces byte-identical logs and SHA-256 hashes. Namespace: adapter /
source key `logforge_demo_firewall`, onboarding session name
`logforge-demo-firewall-v1`, and every raw log carries `device=lfdemo-edge-fw01`.
The onboarding suggestion and the learning run use the **offline** engines,
so the demo needs no API key and no network.

**CLI** — 23 stages, each printed as `[n/23] … [PASS]` or `[FAIL]` with an
actionable hint:

```bash
bash scripts/demo_mode.sh            # full run; human decisions scripted as "demo-operator"
bash scripts/demo_mode.sh --pause    # stop for Enter at every human decision
bash scripts/demo_mode.sh --status   # progress of the 15 lifecycle steps
bash scripts/demo_mode.sh --reset    # remove demo-owned state only
```

(`scripts/demo_mode.sh` runs `python -m app.demo` inside the backend
container, so the host needs no Python.)
The stages are: reset · unknown-vendor log fails · onboarding session with 13
samples · analysis · offline suggestion · sandbox · **approval** · v1 active ·
ingest 8 v1 logs + 1 malformed record · verify SUCCESS, normalization and raw
hash · ingest v2 structure · verify `UNDER_REVIEW` / `DRIFT` · **accept drift** ·
6 v2 evidence logs + learning proposal · learning validation · **approve v2** ·
**activate v2** · ingest a v2 log · ingest an old v1 log · verify both parse via
v2 · **rollback** · verify v1 `ACTIVE`, v2 `ROLLED_BACK` and a new v1 log parsed
by v1 · final summary (demo status complete, timeline, decisions, lineage
verified). The five bold stages are human decisions: each is an explicit API
call recorded with `approved_by` / `by` = `demo-operator`.

**Console (`#/demo`)** — a 15-step lifecycle timeline driven by
`GET /api/v1/demo/status` (derived from stored rows, so it survives reloads).
**Run demo** executes only the automatic steps, through the same public
endpoints, and stops at the next **HUMAN DECISION REQUIRED** box; nothing is
approved, accepted, activated or rolled back until you click that decision.
The evidence panel (events processed, successful, partial, failed, under
review, drift detected/accepted, learning status, adapter version, rollback)
is read from the Views and Learning APIs, and an activity list shows every API
call the page made.

**Idempotency and reset.** Every CLI run starts with a scoped reset, and the
console skips fixtures that were already ingested, so reruns never accumulate
demo state. `POST /api/v1/demo/reset` deletes only rows it can prove are
demo-owned:

- events whose `raw_hash` is the SHA-256 of a demo fixture;
- onboarding sessions named `logforge-demo-firewall-v1` whose samples are all demo fixtures;
- adapter versions, learning sessions, baselines and baseline history of
  `logforge_demo_firewall` — only if every adapter version was created by a
  demo session.

If anything else holds the demo source key, reset refuses (`409`) and the
console shows a namespace conflict. The reset counts non-demo rows before and
after deleting and rolls back if they differ; the response reports both.

## Trust, integration & governance (Phase 7)

Phase 7 is an **additive** layer on top of the frozen Phase 0–6 core: new tables (migration
`0007`), new services and routers, and one hook in ingestion that runs in the same database
transaction as the event insert. The pipeline, normalizer, drift gates, onboarding and learning
services are unchanged. RBAC and audit are attached to the existing governance routers as a
router-level dependency in `app/main.py`.

### Storage model

| Layer | What | Where |
|---|---|---|
| Hot (PostgreSQL) | Event row: metadata, raw payload, SHA-256, normalized representation, **inline** extensions, field accounting, lineage | `events` (unchanged) |
| Extension overflow | Extensions beyond `EXTENSION_INLINE_MAX_FIELDS` / `EXTENSION_INLINE_MAX_BYTES`, stored losslessly with the original key order and a SHA-256 of the canonical payload. Inline ∪ overflow = every preserved field. | `event_extension_overflow`; marker in `processing_metadata.extension_spill` |
| Onboarding evidence | Repeated overflow key structures per adapter, with sample event ids. At `OVERFLOW_EVIDENCE_MIN_OCCURRENCES` they can start an onboarding session. | `overflow_signatures` |
| Cold raw vault | Exact raw bytes, **content-addressed** (`sha256/aa/bb/<digest>`), atomic write and read-only after write. The vault digest must equal the event's SHA-256. A vault failure never loses the event: it records `HOT_ONLY/FAILED` and is retried and alerted. | `RawVault` interface; `FilesystemRawVault` (Docker volume `logforge_evidence`); metadata in `event_raw_storage` |
| Merkle evidence chain | Event hashes sealed into batches (≤ `MERKLE_BATCH_MAX_EVENTS`), RFC 6962-style domain-separated tree, each batch chained to the previous one and written once to an anchor store | `evidence_batches`, `evidence_batch_members`; `AnchorStore` → `LocalWormAnchorStore` |

**Provider pluggability.** Every vault object records its `backend` (in `event_raw_storage`), and
every sealed batch records its `anchor_backend`. Recovery and verification resolve the provider
**by that recorded name** (`raw_vault.BACKENDS`, `anchor.PROVIDERS`), so a future S3/MinIO vault or
object-lock anchor provider can be added next to the local ones. Existing evidence keeps
verifying against the provider it was written to. A recorded provider that this build doesn't
have is reported as `ANCHOR_BACKEND_UNAVAILABLE` or `BACKEND_UNAVAILABLE`, never as valid. Each
provider must pass the contract tests in `test_limitation_fixes.py`. Only
`filesystem` / `local_worm` exist, and `AnchorStore.compliance_grade` is `false` for them.
**No S3/MinIO or object-lock provider is implemented in Phase 7.**

### Integrity verification

- `GET /api/v1/integrity/events/{id}` runs three independent checks: stored SHA-256 vs the raw
  payload, the cold copy's digest, and Merkle inclusion (the proof is returned) plus the anchor
  match. A forgery that rewrites both `raw_event` and `raw_hash` is still caught by the sealed leaf.
- `GET /api/v1/integrity/verify` recomputes every root from its leaves, checks each leaf against
  its `(event_id, raw_sha256)`, chain continuity and sequence, and compares each batch with its
  anchor. Deleted-but-sealed events (e.g. Demo reset) are counted, not treated as failures.
- `GET /api/v1/governance/audit/verify` re-hashes the audit log. Each record's hash covers its
  canonical content plus the previous hash, so edits, deletions and re-hashed forgeries are detected.

### RBAC and maker-checker

| Role | Capabilities |
|---|---|
| `ANALYST` | inspect, review (drift acknowledge, request review), propose (onboarding sessions/suggestions/proposals, learning proposals), export |
| `SECURITY_ENGINEER` | + approve/reject adapters, accept drift as variant, approve/reject/activate learning, rollback, seal, vault backfill, alert sweep |
| `SOC_ADMIN` | + governance configuration, role management, **critical approvals** (replace baseline, HIGH-risk or `confirm_supersede` learning approvals), demo reset |

- Local users with PBKDF2-HMAC-SHA256 hashes (`PASSWORD_HASH_ITERATIONS`) and opaque bearer
  tokens. Only token hashes are stored. The first SOC_ADMIN is created with
  `POST /auth/bootstrap`, which is refused once any user exists.
- **Maker-checker:** for onboarding approval and learning approve/activate, an authenticated user
  who suggested, proposed or edited the object cannot approve it.
- **Identity binding:** when a token is presented, free-text identity fields (`approved_by`,
  `requested_by`, `by`, …) must equal the signed-in user.
- `RBAC_MODE=permissive` (default, for development and Demo Mode) keeps anonymous calls
  working, audited as `anonymous`. `RBAC_MODE=enforce` requires a token for every governed
  action. User management and configuration are never anonymous.
- **Production gate:** with `APP_ENV=production` the process refuses to start unless
  `RBAC_MODE=enforce`. Permissive mode logs a warning at startup, and `GET /auth/status`
  reports `production_safe`.
- **Login throttling:** after `LOGIN_MAX_FAILURES` failed logins for a username within
  `LOGIN_LOCKOUT_MINUTES` (counted since that user's last successful login), further attempts get
  `429` with `Retry-After`, even with the correct password. Failures are counted from the
  hash-chained audit log, so no extra table is needed and the counter cannot be quietly reset.
  The trade-off: an attacker can temporarily lock a known username.
- Every governed action is audited with actor, role, action, object, timestamp, decision
  (`SUCCESS`/`DENIED`/`FAILED`), request evidence and the maker-checker outcome. This covers
  onboarding, drift, learning, rollback, RBAC and configuration changes, exports, raw recovery,
  sealing and alert acknowledgement.

### Review SLA

Drift events `UNDER_REVIEW`, validated onboarding sessions and open learning sessions each get
a **durable deadline** from their severity (`SLA_HOURS_*`, overridable by SOC_ADMIN). Their
status moves `PENDING → DUE_SOON → OVERDUE → ESCALATED` (repeated escalations are counted) and
becomes `RESOLVED` when a human decides. Overdue and escalation raise alerts. **A timeout never
approves, activates or rejects anything:** the known-good adapter version and baseline stay in
force.

### Confidence evidence ledger

For every onboarding or learning proposal version, the ledger records the stated confidence,
sample count, in-sample sandbox result, structural coverage and diversity, and two further
checks:

- **Holdout:** the offline analyzer is re-derived on a 75% training split and tested on the
  held-out samples. For learning, historical structures serve as the holdout.
- **Mutation tests:** value, reorder and injection robustness plus truncation fault detection,
  run through the real pipeline.

The human decision and production outcome are joined in live. `/confidence/calibration` tallies
outcomes per confidence band. This is an evidence ledger, **not a statistical calibration or a
trained model**, and it changes no gate. The offline analyzer remains the default, and live
Claude remains optional and unverified.

### Alert bus

Persisted, deduplicated alerts (`OPEN`/`ACKNOWLEDGED`, read/unread, occurrence count) for:
critical drift, overdue and escalated reviews, learning regression (a learned version with a
materially worse partial/failed rate than its predecessor; rollback stays a human action),
Merkle integrity failure, broken audit chain, raw vault failure, extension-overflow threshold
and parser-failure spikes. Internal delivery is always on. Webhook, Slack, Teams and SMTP
adapters are optional and never expose their destinations. They are tested over real sockets
against local HTTP and SMTP servers: payload shape, HTTP errors, timeouts and unreachable hosts
are recorded, never raised. They have **not** been verified against the hosted Slack, Teams or a
production mail relay.

### Background scheduler

An in-process daemon thread (`SCHEDULER_ENABLED`, `SCHEDULER_INTERVAL_SECONDS`) runs:
vault backfill/retry → Merkle sealing (events older than `MERKLE_SEAL_GRACE_SECONDS`) →
SLA sweep → alert checks. A Postgres advisory lock allows only one tick at a time across
processes.

### Phase 7 API

| Area | Endpoints (`/api/v1`) |
|---|---|
| Auth / RBAC | `GET /auth/status`, `POST /auth/bootstrap`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `GET/POST /auth/users`, `PATCH /auth/users/{username}` |
| Governance | `GET/PUT /governance/config`, `GET /governance/reviews`, `GET /governance/audit`, `GET /governance/audit/verify`, `GET /governance/policy`, `POST /governance/scheduler/run` |
| Integrity | `GET /integrity/status`, `POST /integrity/seal`, `GET /integrity/verify`, `GET /integrity/batches`, `GET /integrity/events/{id}`, `GET /integrity/raw/{id}`, `GET /integrity/raw/{id}/recover`, `POST /integrity/raw/backfill`, `GET /integrity/extensions/{id}`, `GET /integrity/overflow/stats`, `GET /integrity/overflow/evidence`, `POST /integrity/overflow/evidence/{id}/onboarding` |
| Alerts | `GET /alerts`, `GET /alerts/counts`, `GET /alerts/channels`, `POST /alerts/{id}/ack`, `POST /alerts/{id}/read`, `POST /alerts/sweep` |
| Export | `GET/POST /export/events`, `GET /export/schema`, `GET /export/logs` — contract in [docs/export-schema.md](docs/export-schema.md) |
| Confidence | `GET /confidence/onboarding/{id}`, `GET /confidence/learning/{id}`, `GET /confidence/calibration` |
| Views | `GET /views/trust` (read-only): overflow, hot/cold raw distribution, integrity, reviews, confidence, audit, alerts, export activity. `EventRow` gains `extension_storage` / `overflow_field_count`; lineage gains an `evidence` block. |

All are documented in the OpenAPI spec at `/docs`.

### Air-gap implications

Everything in Phase 7 works fully offline: filesystem vault and anchors, local auth, in-process
scheduler and internal alerts. Only the optional alert delivery adapters (and the optional
Claude provider) make outbound calls, and only when explicitly configured. The Docker volume
`logforge_evidence` holds the vault and anchors and must be backed up together with PostgreSQL.

## Environment variables

See `.env.example`. All configuration is via environment variables loaded
through `pydantic-settings`; there is **no authentication** in this MVP (not
in the PRD's MVP scope). Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (set automatically in docker-compose) |
| `CORS_ORIGINS` | Comma-separated allowed origins for the frontend |
| `DEBUG` | Logging verbosity only — never enables stack-trace leakage (see Error handling) |
| `DRIFT_ENABLED` | `true` (default) enables drift detection; `false` restores Phase 0-4 ingestion behavior exactly |
| `DRIFT_SIMILARITY_THRESHOLD` | `0.0`–`1.0` (default `0.85`); vendor events scoring below it vs. their baseline become `UNDER_REVIEW` |
| `DRIFT_CRITICAL_FIELDS` | Comma-separated OCSF targets (default `event_action,severity,network.src_ip,network.dst_ip,network.src_port,network.dst_port`) whose removal or type change always forces review |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `ONBOARDING_LLM_MODEL` | Optional Claude provider for onboarding suggestions and the learning assistant (`claude-opus-5` by default) when a key is set; otherwise the offline engines. Never used by the runtime pipeline |
| `ONBOARDING_MIN_MATCH_RATE`, `ONBOARDING_REJECT_BELOW_MATCH_RATE`, `ONBOARDING_MIN_MAPPING_COVERAGE` | Sandbox thresholds (defaults 0.90 / 0.50 / 0.30) |

## Adaptive unknown-vendor onboarding (Phase 3)

> LogForge can learn a completely new log source from a small sample,
> validate the proposed parser safely, require human approval, version the
> approved mapping, and automatically process future logs.

```
UNKNOWN SOURCE → samples (10–15 recommended) → deterministic multi-sample analysis
→ AI suggestion (untrusted) → strict schema + evidence review → sandbox on EVERY sample
→ PASSED / NEEDS_REVIEW / REJECTED → HUMAN APPROVAL → versioned adapter (ACTIVE)
→ future logs parsed by the normal pipeline — no LLM at runtime
```

**AI is used for onboarding; deterministic, approved configuration is used at runtime.**

1. **Samples.** `POST /onboarding/sessions` with raw `samples` and/or `event_ids`
   of existing events (e.g. the `FAILED` events an unknown source produced).
   Up to 50 samples of ≤ 16 KB; fewer than 10 is accepted with a warning.
   Samples are stored with their SHA-256 and are never modified or dropped,
   whatever fails later.
2. **Multi-sample analysis** (deterministic, `app/onboarding/analysis.py`):
   format per sample (syslog/JSON/CEF via the existing detector and parsers;
   otherwise key=value or delimited structure), every field's presence count,
   value classes (IPv4/IPv6, integer + range, timestamp, epoch, severity word,
   string, …), type consistency, constant values (identity candidates),
   optional fields, structural variants and common prefix. Only observed
   fields are reported.
3. **Suggestion.** Claude (`claude-opus-5`, JSON-schema structured output,
   server-side refusal fallback) when `ANTHROPIC_API_KEY` is set, or the
   deterministic offline analyzer. The samples are sent to the model as
   untrusted data; its output is never executed. The Claude path is covered
   by mocked-HTTP tests only — live Claude inference has not been verified.
   The offline analyzer is fully functional and is what Demo Mode uses.
4. **Proposal = declarative data only** (`app/onboarding/proposal.py`): vendor,
   product, format, parser strategy (`native` for syslog/JSON/CEF, or the
   configuration-only `kv` / `delimited` parsers — no regex, no code), an
   identity match rule, and `raw_field → target` mappings with per-mapping
   confidence and evidence. The schema forbids unknown keys; targets must be
   in the universal-schema allow-list (`network.*`, `user.*`, `process.*`,
   `event_action`, `severity`, `timestamp`, …); raw fields must occur in the
   samples. Invalid mappings are rejected individually and their fields stay
   in `extensions`.
5. **Sandbox validation** (`app/onboarding/sandbox.py`) runs every sample
   through the *real* runtime pipeline with the candidate adapter and reports
   match rate, parsed/failed samples, mapping coverage, unmapped (extension)
   fields, normalization warnings, per-mapping presence ("rule present in 4/12")
   and structural consistency (Phase 5 comparator). Result:
   - `PASSED` — match rate ≥ `ONBOARDING_MIN_MATCH_RATE` (0.90), coverage ≥
     minimum, no rejected mappings, no warnings → *eligible* for approval;
   - `NEEDS_REVIEW` — valid but below a threshold → revise (re-suggest or
     `PUT /proposal`);
   - `REJECTED` — invalid proposal (never executed), match rate below the
     floor, or samples already owned by an existing adapter (a known source is
     a Phase 5 drift matter, not onboarding).

   PASSED means the proposal met the configured thresholds on these samples —
   not that it is correct for every future log.
6. **Human approval (mandatory).** A suggestion is never active; a PASSED
   validation is still not active (`activation.state:
   NOT_ACTIVE_AWAITING_APPROVAL`). Only `POST .../approve` with the exact
   `proposal_version` activates it, after re-running the sandbox. The record
   keeps who (`approved_by`, free text — there is no authentication), when,
   the proposal version, the validation result/match rate and the mapping.
   `reject` activates nothing and keeps the samples; a rejected session can
   receive a new proposal.
7. **Versioned adapters.** Approval creates an immutable row in
   `onboarded_adapters` (`adapter_id` = `vendor_product` unless the approval
   names another valid id, version 1, 2, …). A
   new version supersedes the previous one (kept, never overwritten);
   identical versions are refused; shipped adapter ids can't be claimed; the
   database enforces one ACTIVE version per adapter. `rollback` withdraws the
   active version and reactivates the previous one.
8. **Future auto-parsing.** The ingestion service builds its registry from
   shipped YAML + ACTIVE onboarded adapters. Known formats select the
   onboarded adapter by its match rule; logs the detector can't classify are
   offered to approved `kv`/`delimited` adapters before being marked `FAILED`.
   Events carry `processing_metadata.adapter_source: "onboarded"`, preserve
   raw + hash + unmapped fields, and — as known sources — get Phase 5 drift
   detection. Existing `FAILED` events can be fixed with `/reprocess`.

Every session response includes an evidence-based `explanation` (format and
share of samples, why the vendor was suggested, each mapping with its
presence count, uncertain fields, sandbox result, why it passed/failed, and
exactly what becomes active on approval).

## Continuous adaptive learning (Phase 6)

Phase 5 detects that a known source changed; Phase 6 learns that change —
only when a human has accepted it, and only through explicit approval and
activation. The production runtime stays deterministic: learning is never on
the ingestion path, and no LLM ever touches runtime parsing.

**Eligibility.** A Phase 5 `DRIFT` event whose drift a human accepted
(`add_variant` or `replace_baseline`), on an **onboarded** adapter. Unreviewed,
`acknowledged` and `POSSIBLE_FORMAT_DRIFT` events are not learnable; shipped
YAML adapters (Cisco/Fortinet/Palo Alto) are frozen and never evolved.
Accepting a variant alone never creates a version — learning is a separate,
explicit action (`POST /events/{id}/learning/propose`).

**Evidence** (stored events, referenced by id + SHA-256 — no copies): the
drifted events with exactly the new structure, and recently processed events
of previously accepted structures (regression evidence), plus the drift
snapshot, the old and new structural fingerprints and the baseline's
approved variants.

**Learning = the minimal declarative delta** from the current version
(`app/learning/engine.py`; never code, never regex):

| Mode | Learned as |
|---|---|
| `FIELD_ADDITION` | map the new field when its name follows a target's convention and its values fit (HIGH if present in all drifted samples, MEDIUM otherwise); else keep it in extensions |
| `FIELD_REMOVAL` | keep the mapping (historical meaning is never deleted) and record the field in `optional_fields` |
| `SEMANTIC_REMAP` | a removed mapped field and an added field with the same value class whose name follows the same target's convention (HIGH) or occupies the same position (MEDIUM); the target never changes. For field-map targets the new field is added as an alias, so historical logs keep normalizing |
| `FIELD_TYPE_CHANGE` | keep the mapping only if the new values still fit the target/coercion; otherwise stop mapping it (→ extensions) and require a human mapping decision |
| `FIELD_ORDER_CHANGE` | nothing to learn (`NO_CHANGE_REQUIRED`) |
| `FORMAT_DRIFT` | never learned automatically (needs review) |

Every mapping lists its evidence (e.g. "'username' observed in 12/12 drifted
samples", "values: string", "name follows the user.name naming convention").
**Confidence grades the evidence behind a mapping, not the sandbox result:**
`HIGH`/`MEDIUM` are assigned only by the deterministic engine (naming
convention + value class + presence in the drifted samples); `LOW` is assigned
only to assistant suggestions. Sandbox results (match rate, compatibility,
preservation) are reported separately in `validation`.
An optional LLM assistant (Claude when `ANTHROPIC_API_KEY` is set) may only
suggest targets for *unresolved added* fields; its output is strictly
schema-validated, filtered against the allow-list and evidence, gets LOW
confidence, and any failure falls back to the deterministic result.

**Safety review** rejects (never executes) a delta that has unknown keys,
maps a field that wasn't observed or wasn't changed by the approved drift,
uses a non-allow-listed target, maps a target another present field already
has, or changes an existing mapping's meaning.

**Learning sandbox** (`app/learning/validation.py`) runs the candidate
version through the real pipeline on the drifted events (match rate ≥
`ONBOARDING_MIN_MATCH_RATE`, 90%) **and** the historical events, which must
normalize identically under the new version (backward compatibility). It
also verifies raw + SHA-256 preservation and that every matched sample
produced a normalized event. Result: `PASSED` / `NEEDS_REVIEW` / `REJECTED`
(thresholds reuse the Phase 3 settings). If only backward compatibility is
below threshold, approval requires `confirm_supersede: true`.

**State machine** (`learning_sessions.status`):

```
PROPOSED → VALIDATED | NEEDS_REVIEW | FAILED | NO_CHANGE_REQUIRED
VALIDATED → APPROVED → ACTIVE → ROLLED_BACK        (REJECTED from any open state)
```

Invalid transitions return `409` (e.g. activate without approval, anything
after REJECTED). Activation is idempotent (`ACTIVE → ACTIVE` changes
nothing), locks the session row, refuses a proposal whose base version is no
longer active, refuses a mapping identical to any existing version, and the
Phase 3 one-ACTIVE-version index makes two active versions impossible.

**Activation** creates the next immutable version in `onboarded_adapters`
(previous version `SUPERSEDED`; `validation_summary.origin =
"phase6_learning"` + the learning session id answers "why does v2 exist?"),
and closes the loop with Phase 5: the learned structure becomes the source's
baseline reference, the previous reference is kept as an approved variant,
and a `BASELINE_LEARNED` history row is written. Future drift is measured
against the learned structure. **Rollback** reuses Phase 3 semantics (v2
`ROLLED_BACK`, v1 active again); nothing is deleted.

Every session carries its decision history (who/what/when/why), the mapping
diff (added / removed / changed / unchanged), a risk level (LOW / MEDIUM /
HIGH), a recommendation, and a deterministic `report`:

```
Source: acmefw_acmefw          Current adapter: acmefw_acmefw v1 → Proposed v2
Trigger: FIELD_ADDITION        Evidence: 6 drifted event(s), 10 historical event(s)
Change:  + username + sessionid + app
Mapping: + username → user.name [HIGH]  · 'username' observed in 6/6 drifted samples …
Sandbox: 6/6 new samples passed; 10/10 historical samples normalize identically
Critical fields: none affected   Risk: LOW   Recommendation: APPROVE_VERSION
```

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
(`cisco_asa`, `fortinet`, `paloalto_cef`) or by an ACTIVE onboarded adapter;
the adapter id is the `source_key`. Generic fallback adapters (`*_generic`) never get a baseline
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
works the queue (`GET /events?status=UNDER_REVIEW`, or the console's Drift
Queue), updates a shipped adapter's YAML by hand if needed (onboarded adapters
evolve through Phase 6 learning instead), then calls
`POST /events/{event_id}/drift/accept`:

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

Full curl walkthrough: `docs/curl-examples.md`. Narrated Phase 0–4 ingestion
walkthrough: `scripts/demo.sh` (logs in `demo/logs/`). Full lifecycle demo:
[Demo Mode](#demo-mode).

## Error handling

Every non-2xx response uses one consistent envelope:

```json
{"error": {"code": "VALIDATION_ERROR", "message": "...", "fields": {"raw_log": "..."}}}
```

- `422 VALIDATION_ERROR` — request failed Pydantic validation (missing/empty
  `raw_log`, over the 256 KB single-log / 1000-item batch limits, or a
  `raw_log` containing a NUL byte, which Postgres cannot store).
- `404 NOT_FOUND` — unknown `event_id`, session, adapter or source.
- `409 CONFLICT` — an action not allowed in the current state (e.g. activating
  an unapproved learning session, reviewing a non-drifted event, a demo
  reset refused because the demo namespace is not demo-owned).
- `422 UNPROCESSABLE` — a well-formed request the domain rejects (e.g. an
  invalid adapter id or proposal, an invalid Views cursor or time range).
- `502 UPSTREAM_ERROR` — the onboarding suggestion provider failed or was
  unavailable; the samples are preserved and another provider can be tried.
- `500 INTERNAL_ERROR` — an unexpected failure. The client only ever sees a
  generic message; full details (stack trace included) are logged
  server-side only. Debug mode (`DEBUG=true`) controls log verbosity, not
  whether stack traces reach the client — they never do, in any mode.

At the pipeline level (not HTTP errors), a per-event `error_message` string
and `warnings` list explain exactly what happened — e.g. an unparseable
timestamp, a mapping conflict, or a value too long for its column having
been truncated. Any of these downgrades `status` to `PARTIAL`; a total parse
failure is `FAILED`. The raw log and its hash are persisted in both cases.

## Versioning and rollback

- **Shipped adapters** are versioned with the repository (YAML files); they are
  never changed at runtime and are not evolved by Phase 6.
- **Onboarded adapters** are immutable rows in `onboarded_adapters`, one per
  version: Phase 3 approval creates v1; Phase 6 activation creates the next
  version and marks the previous one `SUPERSEDED`. The database allows only
  one `ACTIVE` version per adapter.
- **Rollback** (`/onboarding/adapters/{id}/rollback` or
  `/learning/sessions/{id}/rollback`) marks the active version `ROLLED_BACK` and
  reactivates the previous one. No version, event, session or decision is
  deleted, so "why does this version exist, who approved it and when" stays
  answerable (Adapter Evolution timeline, learning history, session decisions).

## Offline / air-gapped behavior

- The ingestion pipeline, drift detection, the learning engine, the Views
  API and the console make **no external network calls**.
- Without `ANTHROPIC_API_KEY` the onboarding suggestion uses the deterministic
  offline analyzer and learning uses the deterministic engine; Demo Mode uses
  the offline engines explicitly.
- The console loads no external assets (no CDN scripts or web fonts).
- Building the images needs access to a container registry and package
  indexes (`python:3.11-slim`, `node:20-alpine`, `postgres:16-alpine`, pip and
  npm). For an air-gapped site, build or pull the images where network access
  exists and transfer them. Running the stack with networking physically
  disabled has not been tested.

## Testing

```bash
# backend (runs against a dedicated <db name>_test database, never the live one)
docker compose run --rm backend pytest -q
# or, with the stack running:
docker compose exec backend pytest -q

# frontend (Vitest + Testing Library, jsdom) and production build
docker compose exec frontend npm test
docker compose exec frontend npm run build
```

At the time of writing: **617 backend tests** (457 pre-Phase-7 + 160 Phase 7)
and **36 frontend tests** (23 + 13 Phase 7) pass, and the frontend production
build succeeds.

- `backend/tests/unit/` — parsers, detector, normalizer, adapters, hashing, IDs,
  fingerprinting, the drift comparator, onboarding analysis / proposal schema /
  sandbox / providers (Claude via a mocked HTTP transport), the learning engine,
  and the Demo Mode fixtures (determinism, golden hashes, namespace, v1/v2
  shapes), all with zero DB/network dependency.
- `backend/tests/integration/` — the full FastAPI app against a real Postgres
  instance: ingestion, persistence, reprocessing, batch isolation, failure
  preservation, error envelopes, request-size limits, drift detection and
  human review, the end-to-end onboarding flow (including malformed/unavailable
  LLM and code-injection attempts), the Phase 6 adaptive loop (eligibility,
  every learning mode, delta safety, sandbox + historical compatibility,
  approval/activation/rollback, idempotent and concurrent activation, Phase 5
  baseline feedback, migration round trip), the read-only Views API (including
  a proof that it cannot write), and Demo Mode (full 23-stage run, every human
  boundary, repeated runs, reset idempotency, refusal when the namespace is not
  demo-owned, and preservation of pre-existing non-demo data in every table).
- `frontend/src/*.test.tsx` — every route renders; loading, error and retry
  states; explorer filters, search debounce and reset; forensics verdict,
  lineage and preserved type-mismatch values; drift decisions require an
  explicit confirm; source detail; honest provider labels; export download;
  Demo Mode stops at human decisions, sends the real approval request only on
  click, reads evidence from the Views API, requires confirmation to reset,
  and runs correctly under React StrictMode.

- `backend/tests/unit/phase7/`, `backend/tests/integration/phase7/`,
  `frontend/src/phase7.test.tsx` — extension spill and zero-loss recovery,
  byte-exact raw vault round trips and vault failure/retry, Merkle roots and
  inclusion proofs, chain/anchor tamper detection, export filters /
  pagination / bounded streaming / schema conformance, RBAC, identity binding
  and maker-checker, SLA transitions and escalation, confidence ledger,
  audit hash chain and tamper detection, alerts, the read-only trust view,
  the production RBAC gate, login lockout, provider contract tests for the
  vault and anchors, and alert delivery over real local HTTP/SMTP sockets.

The integration suite uses its own `<db name>_test` database (created
automatically on first run), so running it alongside a live demo never touches
the demo's data.

## Current limitations

**Scope and deployment**
- **RBAC defaults to `permissive`** — governance endpoints still accept
  anonymous calls (audited as `anonymous`; maker-checker cannot be enforced
  between unidentified actors). Set `RBAC_MODE=enforce` to require a signed-in
  role. Demo Mode is designed for permissive mode: under `enforce` with a
  single user, maker-checker blocks the demo's self-approval by design.
  `APP_ENV=production` refuses to start without `RBAC_MODE=enforce`. Local
  accounts only (no SSO/OAuth/FIDO2), with per-username lockout after repeated
  failures but no per-IP rate limiting. Read-only views stay open in both modes.
- **No production deployment profile** — the compose file runs development
  servers (`uvicorn --reload`, Vite dev server).
- **Synchronous ingestion** — each log is processed and committed within its
  API request (batches commit per item, intentionally, so a crash mid-batch
  loses nothing). There is a single API process and a single Postgres
  instance, with no queue or worker tier. No throughput benchmark has been
  published; **billion-events/day is a design target, not a measured result**.
- **Raw payloads stay in PostgreSQL** — the cold vault is a write-through,
  verified copy. Evicting hot raw payloads would change frozen Phase 3/5/6
  readers of `events.raw_event` (lineage, learning evidence, onboarding from
  events, reprocess, search), so it is not done; no storage saving is claimed.
- **Anchors are local files** (O_EXCL, read-only) — WORM-*style*, not a
  compliance-grade immutable store; an administrator with filesystem access can
  still delete them (verification then reports `ANCHOR_MISSING`). The provider
  abstraction is ready for an object-lock provider, but none is implemented.
- **Measured Phase 7 overhead** (Docker Desktop, one API process, 200 events each
  with the vault on and off, interleaved): the per-event vault write (about 3–6 ms
  in isolation, including `fsync`) is lost in the ingest variance. The means were
  13.1 vs 13.2 ms and the p95s 28 vs 28 ms. The confidence ledger adds about
  15–60 ms per *human-initiated* suggestion or proposal, never per event. Sealing
  about 1,200 events takes about 60–100 ms. These are local measurements, not a
  throughput benchmark.
- **No SIEM forwarding and no data-lake integration.** The published
  `logforge.export.v1` contract is the integration boundary.
- **LEEF and XML are not supported.**
- **Live Claude inference has not been verified** — no API key was available.
  The Claude suggestion provider and learning assistant are covered by mocked
  HTTP, schema-validation, refusal/error and fallback tests; no live Claude
  result is claimed. Onboarding and learning are fully functional offline.
- **The console is desktop-first**; below ~960 px navigation becomes a top bar
  and wide tables scroll horizontally inside their cards.

**Parsing and normalization**
- **Vendor mappings don't regex-parse free-text message bodies.** They map
  already-extracted fields declaratively. The one generic exception: the
  syslog parser auto-flattens a `key=value` message payload (any vendor,
  not just Fortinet) so YAML mappings can reference those keys directly.
- **RFC3164 syslog has no year field** — missing years default to the
  current year at parse time (a known limitation of the format itself, not
  of this implementation).
- **No deduplication** — the same raw log ingested twice creates two events
  (the `raw_hash` index exists for traceability lookups, but no dedup logic
  runs).
- **Values that don't fit a typed OCSF group field are not converted.** A
  value that fails type validation for `network.*`, `user.*` or `process.*`
  (e.g. Fortinet `dstport=-`, a numeric JSON `user`) is not mapped: it is kept
  under `extensions` with its original value, a warning is recorded, and the
  event is `PARTIAL`.
- **OCSF-aligned, not OCSF-validated** — events are not checked against the
  full OCSF schema.

**Drift detection**
- **Drift detection is top-level and structural only.** Changes inside
  nested JSON objects surface only as a type change of the parent key;
  value-level changes (e.g. new enum values) are not drift.
- **Format drift detection is evidence-based, not exhaustive.** An event
  that fell back to a generic adapter is flagged `POSSIBLE_FORMAT_DRIFT` only
  if it still carries the vendor's match value (near-miss, other format, or
  in the raw text). A change that removes every trace of the vendor's
  identity is not linked back to the vendor, and a log that merely *mentions*
  a vendor's signature can be flagged (hence "possible"). Logs that no longer
  parse at all stay `FAILED` and are not annotated.
- **Critical fields are checked at the parser (top) level**, resolved
  through the adapter's current mapping; a critical field that is only nested
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

**Onboarding and learning**
- **Declarative parsers cover key=value and delimited single-line records**,
  plus the native syslog/JSON/CEF parsers. Formats that need free-text
  extraction (regex) or multi-line records can't be onboarded — by design, no
  regex or code is ever generated.
- **A source needs a constant identity field** (e.g. a vendor or device name
  present in every sample) so future logs can be recognized safely; the
  offline analyzer refuses otherwise. JSON keys must be simple identifiers to
  be mappable.
- **Learning (Phase 6) evolves onboarded adapters only**; shipped YAML
  adapters are frozen. Learning evidence is limited to the most recent stored
  events (bounded pools), and semantic renames are proposed only on
  name-convention or same-position evidence — anything else stays unresolved
  for a human.

**Demo Mode**
- If non-demo logs that match the demo adapter are ingested while it is
  active, reset leaves those events in place (they are not demo-owned) but
  still removes the demo source's baseline.
- The demo always uses the offline engines; it does not exercise the Claude
  provider.

## Status: current, deferred, future scale architecture

**CURRENT (implemented and tested)**
- Ingestion of syslog (RFC 3164/5424), JSON and CEF; single and batch API.
- Raw preservation, SHA-256 integrity (re-verified by the lineage API),
  OCSF-aligned normalization, unknown-field preservation, field accounting,
  `SUCCESS` / `PARTIAL` / `FAILED` / `UNDER_REVIEW` status handling.
- Shipped vendor adapters (Cisco ASA, Fortinet, Palo Alto) and generic fallbacks.
- Phase 3 unknown-vendor onboarding with sandbox validation and human approval
  (offline analyzer; optional Claude provider, not live-verified).
- Phase 5 structural drift detection, classification, explanation and
  mandatory human review.
- Phase 6 continuous adaptive learning with regression validation, explicit
  approval and activation, versioning and rollback.
- Read-only operational intelligence API (`/api/v1/views`).
- React operational console (Overview, Event Explorer, Event Forensics,
  Sources, Drift Queue, Onboarding, Adapter Evolution, Learning, Export,
  Integrity, Alerts, Audit Log, Governance, Demo).
- Reproducible Demo Mode (CLI and console) with an ownership-verified reset.
- Docker Compose development environment with automatic migrations.

- Phase 7 trust / integration / governance layer (see below): extension
  spill, cold raw vault, Merkle evidence chain with local anchors, streaming
  export with the published `logforge.export.v1` schema, RBAC + maker-checker,
  review SLA with escalation, confidence evidence ledger, hash-chained audit,
  internal alert bus with optional delivery adapters, background scheduler.

**DEFERRED (planned, not implemented)**
- LEEF and XML parsers.
- S3/MinIO raw-vault backend, external WORM/object-lock anchoring, and a
  hot-raw eviction policy (needs a raw resolver in the frozen core readers).
- A production deployment profile.
- A measured throughput baseline and scaling documentation.
- Live verification of the Claude provider.
- SSO/OAuth/FIDO2; PII tokenization for external
  destinations (the PRD's standard flow includes a tokenization step; out of
  scope until a later phase).
- Deduplication, regex / multi-line onboarding, nested-field drift.

**FUTURE SCALE ARCHITECTURE**
- Explicitly deferred per the PRD: Kafka in front of ingestion, horizontally
  scaled workers, a dead-letter queue, OpenSearch, and MinIO (object storage /
  data-lake landing zone).
- Further scale work not yet designed in detail: partitioned / time-series
  event storage with retention, and SIEM forwarding connectors.

These are what a billion-events/day deployment would require; none of them
exist in this codebase today.
