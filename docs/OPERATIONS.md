# Native operations

> **Accuracy notice (defect remediation, 2026-09-29).** Parts of this document describe capabilities the
> application in this repository does not have. Verified against the code: the backend supports **PostgreSQL
> only** (no SQLite); there is **no evidence-signing key or keyed batch signature** (Merkle anchors are unsigned
> local append-only files); ingestion has **no idempotency key, deduplication or `raw_base64`**; there is **no
> drain/readiness endpoint** (only `GET /health`); outbound webhook evidence delivery is **not implemented**; and
> the integrity check is `GET /api/v1/integrity/verify` (all routes are under `/api/v1`). Statements below that
> depend on those features are not valid for this codebase. Authoritative references: README.md,
> [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CAPABILITIES.md](CAPABILITIES.md).

## Local runtime

`scripts/run_local.py` loads the root `.env` without executing it, initializes the database, and starts the API, persistent worker, and Vite UI. The API binds to loopback by default. The SQLite path is relative to the backend directory, so the normal local database is `backend/data/logforge.db`.

Use the supplied launcher consistently; manually starting `uvicorn` from a different directory with a relative SQLite URL points at a different database. An absolute SQLite URL or PostgreSQL URL avoids this ambiguity.

The launcher's `--backend-only` option starts API plus worker. `--migrate-only` initializes the schema and exits. Start the native console with `scripts/start.ps1` or `bash scripts/start.sh`.

## Configuration

| Variable | Purpose / default |
| --- | --- |
| `DATABASE_URL` | Local `sqlite:///./data/logforge.db`; production `postgresql+psycopg://user:password@host:5432/logforge` |
| `LOGFORGE_ENV` | `development`; production mode requires PostgreSQL and a nondefault secret |
| `LOGFORGE_SECRET_KEY` | At least 32 characters in production; generated privately by setup |
| `LOGFORGE_ALLOWED_ORIGINS` | Explicit comma-separated console origins; wildcard forbidden in production |
| `LOGFORGE_MAX_ACTIVE_REQUESTS` | Per-process request capacity; default 32 |
| `LOGFORGE_SESSION_HOURS` | Session lifetime; default 8 |
| `LOGFORGE_REVIEW_SLA_HOURS` | Pending-review SLA; default 24 |
| `LOGFORGE_ALLOW_PRIVATE_WEBHOOKS` | `false`; opt in only for deliberately trusted internal test receivers |
| `LOGFORGE_WEBHOOK_HOSTS` | Comma-separated destination allowlist; mandatory for production forwarding |

Existing process environment values take precedence over `.env`. Production service managers should inject secrets from the operating system or your secret manager. `.env` is ignored by version control. The generator refuses to overwrite an existing file, since changing the evidence-signing key casually would invalidate historical signatures.

The current signature implementation uses one configured key. Plan key rotation as a versioned-signature migration with preserved historical verification keys; do not replace the key and assume old evidence will still verify. Versioned signing-key rotation is a documented future hardening item.

## Production process setup

Install PostgreSQL and create a dedicated database/account with only application-schema permissions. Install Python dependencies into an isolated environment. Set production variables, back up the existing schema, then run this once from `backend`:

```text
python -m app.cli migrate
python -m app.cli create-user --username admin --role admin --password-env LOGFORGE_BOOTSTRAP_PASSWORD
```

Run the following as separate supervised native services from `backend`:

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --timeout-graceful-shutdown 50 --timeout-keep-alive 5
python -m app.worker
```

Use systemd, launchd, or an appropriate Windows service manager for restart policy, service identity, memory/CPU controls, and secure environment injection. The repository does not install machine-wide services automatically. Use a single worker initially; measure database lock pressure before introducing more worker processes.

The schema initializer is an idempotent version-9 bootstrap for this newly created project. It is not a verified migration chain from an unrelated preexisting Phase-1–8 installation. Production schema changes require explicit reviewed migrations, backups, and staging restore tests.

Build the frontend with `npm run build` or `pnpm run build` in `frontend`. Setup uses npm if available, then pnpm from PATH or the Codex bundled fallback. The committed `frontend/pnpm-lock.yaml` records the resolved frontend packages; `frontend/pnpm-workspace.yaml` allows the esbuild build step. CI installs that lockfile with pnpm 11.19.0. Python setup applies `backend/requirements.lock.txt` as constraints over platform-appropriate requirement extras. Terminate HTTPS at your organization's reverse proxy and expose only the intended entry point. The bundled NGINX configuration binds to loopback HTTP so it is suitable behind such an edge proxy or for local validation. Site-specific certificates and public network exposure are deliberately not configured.

## Optional NGINX replicas

Install native NGINX, add its executable to `PATH`, and build `frontend/dist`. Run the same API on ports 8001, 8002, and optionally 8003/8004, with the same PostgreSQL and signing-key configuration.

PowerShell:

```powershell
$env:LOGFORGE_BACKEND_ADDRESSES = '127.0.0.1:8001,127.0.0.1:8002'
.\.venv\Scripts\python.exe deploy\health_router.py
```

Unix:

```bash
LOGFORGE_BACKEND_ADDRESSES=127.0.0.1:8001,127.0.0.1:8002 .venv/bin/python deploy/health_router.py
```

The companion generates runtime files below `deploy/runtime`, starts its own NGINX master, probes readiness every two seconds, and updates healthy upstreams. Set `LOGFORGE_NGINX_BIN` for an explicit executable path; set `LOGFORGE_NGINX_PREFIX` for a different writable runtime directory. Static address entries must be IPv4 address/port pairs. For DNS discovery, set `LOGFORGE_BACKEND_ADDRESSES` to an empty string and configure `LOGFORGE_BACKEND_HOST`/`LOGFORGE_BACKEND_PORT`.

`GET /router/health` reports current discovery, healthy pool size, and freshness. `GET /router/metrics` exports controller counters. Access logs in `deploy/runtime/logs/access.log` include request duration, upstream duration, upstream address, HTTP status, and sizes, without tokens or raw log content. Summarize them with `python scripts/router_report.py`.

The companion is provided for native NGINX validation. It has not been deployed on this delivery host; its configuration, reload behavior, and platform signal/process behavior must be exercised on the target operating system before production use. Unit tests cover upstream validation and empty-pool behavior only.

## Health, saturation, and drain

- `/api/health/live`: process liveness.
- `/api/health/readiness`: database availability plus readiness/draining state.
- `/api/metrics`: public Prometheus-style operational values; no raw evidence or credentials.
- `/api/admin/drain` and `/api/admin/undrain`: authenticated administrator controls for the replica receiving the request.

To drain one replica, send the drain request directly to that replica's loopback port, wait for the router's healthy count to decrease and active work to finish, then stop that replica. Do not send a per-replica drain request through a load-balanced URL and assume it selected the intended worker. During saturation or temporary transport failure, retry with backoff. Ingestion has no idempotency key: if the first attempt was committed but its response was lost, the retry creates a second event (same `raw_hash`).

API metrics are per process where noted; scrapes through a round-robin address are not a fleet total. Scrape each replica when collecting process-level telemetry. Use PostgreSQL `pg_stat_database`, connection metrics, storage/WAL telemetry, and database host CPU/I/O to locate persistence bottlenecks. The shared write lock protects evidence order but constrains ingestion concurrency. More API replicas cannot remove that constraint.

## Backups and restore

Use a dedicated backup location with access controls and encryption appropriate to the log data:

```text
python scripts/backup.py --output backups/logforge-snapshot.backup
```

The helper creates an online SQLite backup or PostgreSQL custom dump and SHA-256 sidecar. It refuses to overwrite an existing file. For PostgreSQL, install `pg_dump` from a compatible PostgreSQL client distribution; TLS can be supplied with standard `PGSSL*` environment variables. Back up the signing secret separately under controlled custody; never append it to the database dump metadata.

Restore into a **new** database first. For SQLite, stop the API/worker, copy the verified snapshot to a new database path, and point a temporary instance at it. For PostgreSQL, use `createdb` and `pg_restore --no-owner --dbname <new-database> <snapshot>` with credentials in standard PostgreSQL environment variables. The project deliberately does not provide an unattended destructive overwrite command.

Verify the SHA-256 sidecar before restore. After restore, use the same historical signing secret, run `/api/integrity`, inspect representative original/replay revisions and Merkle proofs, authenticate with the intended user accounts, and resume a paused replay in a recovery test. Measure your actual recovery time and restore point. A backup file alone is not proof of recoverability.

## Retention and archive

Raw vault records, events, batches, revisions, and audit records form a connected evidence graph. Deleting old events independently can break batch or audit verification. There is no automatic production deletion job in this release.

Define source-specific retention, legal holds, archive access, and disposal approval before implementing cleanup. Export JSON/NDJSON with the relevant hashes, Merkle roots/proofs, revision history, and audit anchors; verify the copied evidence before reducing primary storage. Establish an independently controlled archive if stronger custody is required. A database backup, local file, SHA-256, or Merkle root does not by itself provide WORM enforcement. Partitioning, automated verified archival, and retention deletion are deferred until a measured storage requirement justifies their schema and governance changes.

## Failure response

Parser failures remain stored as failed evidence. An ingestion database failure fails the request; retry the identical batch after recovery (there is no idempotency key, so a retry after a lost response can duplicate events). Keep a durable copy of unacknowledged input at the sending system. A source-to-API transport is not itself a durable queue.

If the worker stops, restart it against the same database and inspect persisted replay/delivery states. If an integrity check fails, preserve database snapshots and audit evidence, investigate the failed IDs, and do not repair by recalculating authoritative hashes. Alerts and the review ledger expose operational and approval issues; external paging integration requires site-specific setup.
