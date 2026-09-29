# Validation and measured performance

> **Accuracy notice (defect remediation, 2026-09-29).** Parts of this document describe capabilities the
> application in this repository does not have. Verified against the code: the backend supports **PostgreSQL
> only** (no SQLite); there is **no evidence-signing key or keyed batch signature** (Merkle anchors are unsigned
> local append-only files); ingestion has **no idempotency key, deduplication or `raw_base64`**; there is **no
> drain/readiness endpoint** (only `GET /health`); outbound webhook evidence delivery is **not implemented**; and
> the integrity check is `GET /api/v1/integrity/verify` (all routes are under `/api/v1`). Statements below that
> depend on those features are not valid for this codebase. Authoritative references: README.md,
> [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CAPABILITIES.md](CAPABILITIES.md).

## What counts as evidence

Run results are tied to a code revision, runtime, database, workload, and host. Source code, a benchmark tool, and a CI workflow are deliverables; they are not substitutes for a successful run. The delivered environment provides native Python/Node and local SQLite. PostgreSQL and native NGINX deployment validation require those services to be installed separately.

The operational helper suite contains four passing deterministic checks: address validation/deduplication, fail-closed empty upstreams, percentile calculation, and unique mixed-format workloads. Python operational/deployment scripts passed syntax compilation. The package-manager resolver was checked against the installed bundled pnpm path. Backend and frontend final run results are recorded by the main project validation task and should be consulted alongside the test output.

## Recorded native HTTP smoke

The real HTTP run in [native-smoke.json](validation/native-smoke.json) passed using an isolated SQLite database. Eight simultaneous submissions of one 28-event batch produced exactly 28 authoritative events. All 28 were checked for exact raw recovery, hashes, zero field loss, lineage, original revisions, and Merkle proof validity. The drain/undrain readiness check passed. Final integrity covered **11,669 events** with valid audit and batch evidence.

The accompanying [measured benchmark](validation/benchmark-smoke.json) recorded one request at each prescribed batch size:

| Events in batch | Accepted | Request latency | Batch wall-time throughput |
| --- | --- | --- | --- |
| 1 | 1 | 6.8 ms | 145.8 events/s |
| 100 | 100 | 208.2 ms | 477.4 events/s |
| 1,000 | 1,000 | 2,268.6 ms | 438.5 events/s |
| 10,000 | 10,000 | 25,786.4 ms | 386.1 events/s |

A two-second sustained run accepted 540 events in 54 requests without transport errors (approximately 271.4 events/second); request p50/p95/p99 were 31.1/64.4/79.0 ms. These are smoke measurements from one host, not capacity or tail-latency guarantees. Every large batch has only one sample, so its equal p50/p95/p99 values are not a statistical distribution.

The report's external `resources` sampler captured the Windows virtual-environment launcher rather than its API child and is **not valid application resource evidence**. The independently captured API metrics do report the actual backend process: cumulative CPU increased from 1.203125 to 36.078125 seconds (34.875 CPU-seconds during the benchmark and final integrity checks), and observed RSS rose from 85,839,872 to 157,151,232 bytes (81.9 to 149.9 MiB). These are two endpoint samples, not peak memory. The tooling now includes descendants when sampling and records API resource snapshots explicitly. PostgreSQL and NGINX scale behavior were not measured by this SQLite smoke.

The frontend production build passed with Vite 6.4.3 (approximately 301 kB JavaScript and 49 kB CSS before compression). The backend automated suite passed 108 tests at the recorded validation checkpoint; use current test output if later tests expand the suite. Operational helper tests passed 4/4. [Browser interaction and responsive checks](UI_VALIDATION.md) passed against the real local API.

## Reproduce automated checks

From the repository root on Windows:

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Pop-Location
.\.venv\Scripts\python.exe scripts\test_operations.py
Push-Location frontend
npm run build
Pop-Location
```

On Linux/macOS use `.venv/bin/python`; run the backend suite from `backend`. Use `pnpm run build` if npm is not installed. The operations suite has no running-service dependency. CI runs all three checks on a native Ubuntu runner, using the backend version constraints and the pnpm lockfile.

## Real HTTP benchmark

Start the project and create an account with ingestion and integrity access. Put its password in `LOGFORGE_BENCHMARK_PASSWORD` or put an existing bearer token in `LOGFORGE_TOKEN`. Do not pass secrets in shell arguments.

```text
python scripts/benchmark.py --url http://127.0.0.1:8000 --username admin --output artifacts/benchmark-local.json
```

Default workload: batch sizes 1, 100, 1,000, 10,000; three requests per batch size; 30 seconds of sustained traffic; two client workers; 100 events per sustained request. It includes Syslog, CEF, LEEF, XML, extension-heavy JSON, malformed evidence, and configurable payload padding. Every request has a fresh idempotency key and unique raw payload markers. Parser failures are expected evidence outcomes for malformed inputs; transport/persistence errors are failures.

The JSON report contains accepted events, elapsed wall-clock time, events/second, request p50/p95/p99, transport errors, before/after API metrics, and the final integrity result. Latencies are **request** latencies; dividing them by batch size would not produce a meaningful per-event latency percentile. Three batch samples are enough for a smoke check, not a statistically strong tail estimate. Increase `--repeats` and sustained duration for capacity studies.

Use `--server-pid <pid>` repeatedly to capture CPU and RSS across known backend process trees, including Windows virtual-environment launcher children. Shared descendant PIDs are counted once; aggregate RSS can still count memory pages shared across processes. Without explicit PIDs the script labels server resource data unavailable. Set `LOGFORGE_BENCHMARK_DATABASE_URL` privately to collect read-only PostgreSQL activity snapshots. Missing database permission or tooling is recorded as unavailable, not zero.

Example small smoke run:

```text
python scripts/benchmark.py --batch-sizes 1 100 --repeats 1 --sustained-seconds 2 --sustained-batch 10 --concurrency 1 --output artifacts/benchmark-smoke.json
```

Benchmarking adds real evidence to the configured database. Use a disposable test database or a designated benchmark source; the script does not delete evidence afterwards.

## Native 1/2/4-replica and recovery verification

Prerequisites: PostgreSQL, native NGINX, frontend production build, private `.env` configured for PostgreSQL, and unused local ports 8001–8004, 8080, and 9080. The test refuses to call SQLite a scale test. It creates a unique administrator and test sources, and starts/stops only its own child processes.

```text
python scripts/verify_scale.py --output artifacts/scale-verification.json
```

For each replica count, the script checks concurrent retries return the same authoritative event IDs; then checks stored raw bytes, SHA-256, vault recovery, field accounting, normalized output presence, lineage, original revisions, and Merkle proofs. It requires all active replicas to appear in upstream request logs and runs full integrity checks.

With two and four replicas, it continuously submits uniquely keyed traffic, kills one process, observes that the readiness controller removes it, verifies healthy replicas remain available, restarts the process, and waits for pool recovery. The same payload/key is retried after transient failures. A separate drain/undrain exercise verifies intentional removal and recovery. Logs and the JSON result are retained under `artifacts`.

**This target-environment test has not been executed on the delivery host.** NGINX configuration syntax, process management, real PostgreSQL lock behavior, and recovery after native process termination need this test before any deployment claim. Native NGINX worker behavior differs by operating system; validate on the actual production platform.

## Remaining release gates

- Target PostgreSQL backup/restore drill with secret custody and post-restore integrity verification.
- Native NGINX 1/2/4 routing plus failure recovery as above.
- A realistic-duration benchmark with representative production payload sizes, known CPU/RSS collection, database/WAL/fsync telemetry, and meaningful tail samples.
- Site-specific TLS, service restart policy, secret storage, outbound network controls, and external webhook receiver verification.
- Independent security/compliance assessment where the deployment requires one.

These are named limitations, not implied successes. They do not prevent running and evaluating the complete local application.
