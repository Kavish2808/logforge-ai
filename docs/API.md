# API and integration guide

Run the backend and open `/docs` for the generated, authoritative schema. Every application route is under `/api/v1`; `GET /health` is public. Errors use one envelope: `{"error": {"code", "message", "fields"}}`.

## Authentication and RBAC

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username":"admin","password":"your-private-password"}
```

Use the returned `access_token` as `Authorization: Bearer <token>`. Tokens are stored only as SHA-256 hashes, expire (`AUTH_TOKEN_TTL_MINUTES`) and are revoked by `POST /api/v1/auth/logout`. The first SOC_ADMIN is created with `POST /api/v1/auth/bootstrap` (refused once any user exists). Never put credentials in URLs or commit request files containing passwords.

Roles: `ANALYST` (inspect, review, propose, export) < `SECURITY_ENGINEER` (+ approve adapters/drift/learning, rollback) < `SOC_ADMIN` (+ governance configuration, user management, critical approvals). `GET /api/v1/auth/status` reports the mode.

| `RBAC_MODE` | Behavior |
|---|---|
| `permissive` (default; development and Demo Mode only) | Anonymous calls to operational endpoints — ingest, review, propose, approve, export, replay — are accepted and audited as `anonymous`; maker-checker cannot be enforced between anonymous callers. Any presented token is fully enforced (invalid token → `401`, missing capability → `403`). |
| `enforce` | Every governed action and every export requires a token with the needed capability; every ingest call (`/ingest`, `/ingest/batch`, `/ingest/demo`) requires a valid token of any role. |

In both modes: user management, governance configuration, critical approvals, golden baselines and webhook integrations always require an authenticated role, and read-only views (`/views/*`, `GET /events*`, the `/integrity` status/verify/batch/event reads, `/dashboard`) stay open. Raw recovery from the cold vault (`GET /integrity/raw/{id}/recover`) needs the `inspect` capability (anonymous only in permissive mode). `APP_ENV=production` refuses to start unless `RBAC_MODE=enforce`.

Identity fields in request bodies (`approved_by`, `rejected_by`, `requested_by`, `activated_by`, `by`, `submitted_by`) must equal the signed-in user when a token is presented (`403` otherwise). When omitted on an approval-type decision (approve, reject, activate, rollback, request-review), the decision is recorded with the acting identity: the signed-in username, or `anonymous` in permissive mode.

## Ingest

```http
POST /api/v1/ingest
Content-Type: application/json

{"raw_log": "CEF:0|Acme|Firewall|1|100|Allowed connection|5|src=192.0.2.10 dst=198.51.100.20 act=allow", "source_hint": "optional"}
```

`raw_log` is UTF-8 text, 1–256,000 characters, without NUL characters. `POST /api/v1/ingest/batch {"logs": [{"raw_log": ...}, ...]}` accepts 1–1,000 logs; each is ingested independently, so one bad log never aborts the batch. The response carries the stored event(s) and per-batch counts (`success_count`, `partial_count`, `failed_count`, `under_review_count`).

Acceptance means the evidence was persisted, not that its format was understood: each event has its own `status` (`SUCCESS`, `PARTIAL`, `FAILED`, `UNDER_REVIEW`). There is no idempotency key and no deduplication: each accepted log becomes a new event, even when byte-identical to an earlier one (both share the same `raw_hash`). Binary / non-UTF-8 payloads (`raw_base64`) are not supported.

## Inspect evidence

- `GET /api/v1/views/events` — explorer listing with filters and a keyset `cursor`; `GET /api/v1/events/{id}` — the full event (raw, normalized, extensions, drift, lineage metadata).
- `GET /api/v1/views/events/{id}/lineage`, `GET /api/v1/revisions/{id}`, `GET /api/v1/lineage/compact/{id}` — lineage, revision history and compact lineage.
- `GET /api/v1/integrity/events/{id}` — per-event SHA-256, cold-copy and Merkle inclusion/anchor checks; `GET /api/v1/integrity/raw/{id}/recover` — byte-exact recovery from the cold vault.
- `GET /api/v1/integrity/verify` — the whole Merkle chain (roots, chain links, anchors) **and** a re-hash of every stored raw event: `valid` is false, with `RAW_HASH_MISMATCH` / `EVENT_HASH_NOT_SEALED_LEAF` problems naming event and batch ids, when a raw event no longer matches its hash or its sealed leaf. It never repairs anything. `GET /api/v1/governance/audit/verify` re-hashes the audit chain.

An integrity failure is an investigation signal; do not overwrite hashes to make the check pass.

## Export

`GET /api/v1/export/events?output=ndjson|json&...filters` or `POST /api/v1/export/events` (JSON body). Requires the `export` capability (ANALYST+; anonymous only in permissive mode, audited). `include_raw=true` adds the raw payload. NDJSON ends with a trailer record (`complete: true`); JSON is capped at `EXPORT_JSON_MAX_EVENTS`, NDJSON at `EXPORT_MAX_EVENTS`. Use `next_cursor` as `cursor` for the next page. Contract: [export-schema.md](export-schema.md).

## Review and replay

The console exposes proposal, validation, shadow test, approval, activation, evolution, rollback, baseline/golden review and replay operations; see `/docs` for exact models. Rules enforced for direct API clients:

- An authenticated maker cannot approve or activate their own onboarding proposal or learning session, even with an admin role.
- An onboarding candidate below 50% match is rejected; 50–90% needs review and cannot be approved; at least 90% is eligible for human approval — never automatic activation.
- A shadow `BLOCKED` result cannot activate; `REVIEW_REQUIRED` needs an authenticated SOC_ADMIN with a written note. Missing coverage is reported, not manufactured.
- Replays of up to 10,000 events need the `review` capability (anonymous in permissive mode); more than 10,000 events need an authenticated SECURITY_ENGINEER/SOC_ADMIN creator and a different one to start the job.
- Replay adds revisions; it never replaces raw evidence or erases the original revision.

## Webhook integrations

`GET/POST /api/v1/integrations` and `POST /api/v1/integrations/{id}/deliver` require an authenticated SOC_ADMIN in every RBAC mode; create and delivery requests are audited (`INTEGRATION_CREATE`, `INTEGRATION_DELIVERY`). Registration validates the destination (http/https only; loopback, private, link-local and multicast addresses are refused; the host is resolved at registration time only).

**Outbound evidence delivery is not implemented.** `POST .../deliver` sends nothing and answers `501 NOT_IMPLEMENTED`; there is no request signing, retry, idempotency key or delivery tracking. Registrations are stored in a local JSON file on the API node: not in the database, not replicated across replicas, not included in backups. Use the export API to transfer evidence to a SIEM.

Alert notifications are separate: the alert bus can post alerts to a configured webhook, Slack, Teams or SMTP destination (`ALERT_*` settings) and records each delivery attempt on the alert.
