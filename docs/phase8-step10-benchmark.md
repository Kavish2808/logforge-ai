# Phase 8 Step 10 — Benchmark and Scale Evidence

> **Reading rule.** Every table carries a *Kind*: **MEASURED** = recorded by `python -m logforge_bench` or by the host-side `docker stats` sampler in `scripts/bench.sh` on the environment in section B. **PROJECTED** = a requirement computed from a target (e.g. events/s needed for 1B/day). **EXTRAPOLATED** ("EXTRAPOLATION — NOT MEASURED") = a measured rate carried beyond what was run (e.g. × 86,400 s). **UNKNOWN** = not measured. The current Docker Compose + single PostgreSQL + synchronous-commit architecture has **not** been shown to process 100 million, 500 million or one billion events per day, and this document does not claim it can.

- Suite `suite-20260928T135805Z` (plan `standard`): 46 runs planned, 46 completed, 0 failed.
- Suite `suite-20260928T143154Z-completion` (plan `completion`): 24 runs planned, 24 completed, 0 failed.
- Sustained run: `20260928T145051Z-118d8a` (COMPLETED).
- Artifacts: `backend/logforge_bench/results/` (per-run `result.json`; per-event/per-sample JSONL files are kept locally and git-ignored).

## A. Executive summary

- **Sustained (MEASURED):** 300 s steady state after a 30 s warm-up, HTTP, 4 connections, batch 1, default configuration, scheduler on: **37.40 events/s**, p50 86.643 / p95 221.433 / p99 290.834 ms, 0 errors. Degradation: *NO DEGRADATION DETECTED within the stated thresholds over 300 s of steady state at this load; this does not demonstrate behavior over longer runs or at larger table sizes*.
- **Best full-pipeline (default configuration) throughput in any run (MEASURED):** 58.59 events/s (`L-http-c2-b50`, 2,000 events, single run).
- **Best reduced-configuration run (MEASURED):** 93.74 events/s (`K-V0_core-rep2`, variant `V0_core`) — not the full pipeline.
- **Where the time goes (MEASURED):** the deterministic pipeline alone processes 1,623.26 events/s; with persistence the same data runs at 36.82 events/s — per-event database work dominates.
- **HTTP concurrency (MEASURED, batch 1):** 1 conn → 33.96, 2 conn → 32.89, 4 conn → 38.85, 8 conn → 28.57 events/s against one API process.
- **1B events/day:** requires 11,574.07 events/s on average (PROJECTED requirement); the sustained measurement is 37.40 events/s — 309.5× short. **NOT DEMONSTRATED.**
- **Verdict:** STEP 10 COMPLETE — ALL ACCEPTANCE CRITERIA PASSED

## B. Exact benchmark environment

| Item | Value (MEASURED / recorded) |
|---|---|
| Host OS | Microsoft Windows 11 Home Single Language 10.0.26200 |
| Host CPU | Intel(R) Core(TM) 5 120U; 10 cores / 12 logical |
| Host RAM | 15.7 GiB |
| Docker | client 29.7.2 / server 29.7.2 |
| Docker engine | Docker Desktop; 12 CPUs; 8163377152 bytes; kernel 6.6.87.2-microsoft-standard-WSL2 |
| Container OS / kernel | Linux 6.6.87.2-microsoft-standard-WSL2 |
| CPU model (in container) | Intel(R) Core(TM) 5 120U |
| CPU cores visible in container | 12 |
| RAM visible in container (MB) | 7785.2 |
| cgroup limits (backend container) | {"cpu.max": "max 100000", "memory.max": "max"} |
| Python | 3.11.16 (CPython) |
| Packages | fastapi 0.115.6, uvicorn 0.34.0, SQLAlchemy 2.0.36, psycopg 3.3.6, pydantic 2.10.4, alembic 1.14.0, httpx 0.28.1 |
| PostgreSQL | PostgreSQL 16.15 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit |
| PostgreSQL settings | {"shared_buffers": "128MB", "synchronous_commit": "on", "fsync": "on", "wal_level": "replica", "max_connections": "100", "work_mem": "4MB", "effective_cache_size": "4GB", "checkpoint_timeout": "5min", "track_wal_io_timing": "on", "max_wal_size": "1GB", "wal_buffers": "4MB", "commit_delay": "0"} |
| Benchmark version | 1.0.0 |

**Git SHA:** `2c6a489ebe61b5a61dc9e911d12db94cfdd3c58c` — working tree dirty: yes (3 status entries: the uncommitted Step 10 harness; no application file differs from that commit).

The benchmark database `logforge_bench` shares the PostgreSQL server of the running development stack; the load generator runs in the same container as the API process under test (Docker Desktop VM, shared CPUs). `track_wal_io_timing` is enabled for the `logforge_bench` database only (completion plan and sustained run).

## C. Dataset, seed and reproducibility

Seed **1337** for every run. Largest in-process run (`B-inproc-v3_full-10000`): 10,000 events, 33,047,647 bytes; malformed target 5%, actual **4.47%** (447 events). Event *i* is a pure function of (seed, *i*); formats × size bands interleave round-robin; every well-formed raw log is unique (warm-up events use a disjoint index range). A few malformed kinds are constant strings (e.g. a truncated CEF header), so a handful of byte-identical malformed lines are sent per run — see section M.

| Format | Events | Generated as |
|---|---|---|
| cef | 1,668 | CEF:0 Palo Alto Networks → vendor adapter `paloalto_cef` |
| fortinet | 1,668 | RFC3164 syslog, FORTIGATE key=value → vendor adapter `fortinet` |
| json | 1,664 | single JSON object → `json_generic` |
| leef | 1,668 | LEEF:1.0 TAB-delimited → `leef_generic` |
| syslog | 1,664 | RFC3164 sshd line / RFC5424 key=value → `syslog_generic` |
| xml | 1,668 | flat Windows-style event XML → `xml_generic` |

| Size band | Kind | Target B | Events | min B | median B | p95 B | max B |
|---|---|---|---|---|---|---|---|
| large | MEASURED | 4,096 | 2,500 | 12 | 4,098.00 | 4,104.00 | 4,126 |
| medium | MEASURED | 1,024 | 2,500 | 12 | 1,016.00 | 1,032.00 | 1,058 |
| small | MEASURED | 150 | 2,500 | 11 | 144.00 | 161.00 | 186 |
| xlarge | MEASURED | 8,192 | 2,500 | 12 | 8,181.00 | 8,194.00 | 8,215 |

Extension-heavy: 9,662 events carry extensions (mean 40.37 keys); 4,848 exceed the Phase 7 inline budget (64 fields / 8 KiB) and spill to overflow. Band minimums of 11–12 B are truncated malformed events. Malformed kinds: `garbage_prefix` × 28, `incomplete_leef_header` × 39, `invalid_port_value` × 77, `malformed_leef_attribute` × 32, `mismatched_closing_tag` × 37, `trailing_comma` × 50, `truncated_cef_header` × 38, `truncated_document` × 44, `truncated_object` × 37, `truncated_syslog_header` × 65.


| Aspect | What was done |
|---|---|
| In-process mode | Calls the real `ingestion_service.ingest_raw_log` (or the pure pipeline for `pipeline_only`) in the harness process against `logforge_bench`. Pipeline + service + DB; no HTTP. |
| HTTP mode | A separate `uvicorn app.main:app` (1 worker, no `--reload`, access log off) on 127.0.0.1, bound to `logforge_bench`; one keep-alive HTTP/1.1 connection per client thread; batch > 1 uses `POST /ingest/batch`. |
| Configurations | Existing switches only (`DRIFT_ENABLED`, `RAW_VAULT_ENABLED`, Phase 8 hook registration); V0–V3 are configurations of the current code, not historical releases. |
| Isolation | Each run TRUNCATEs `logforge_bench` and uses its own vault/anchor directory; a name guard refuses to reset any database not ending in `_bench`. The development database is never written. |
| Warm-up | 50–100 events (≥ batch × connections for batch runs) before fixed-size runs, excluded and reported separately; the sustained run reports 30 s of warm-up separately from 300 s of steady state. |
| Statistics | Linear-interpolation percentiles over all samples; nothing trimmed. V0–V3 and the older variant block: 3 interleaved repetitions each; everything else: single runs. |
| PostgreSQL | `pg_stat_database` deltas; cluster-wide `pg_stat_wal` / `pg_stat_bgwriter` deltas; `pg_stat_activity` sampled every 0.5 s; container CPU/memory from host `docker stats`. |
| Integrity | Completion plan and sustained run: row counts vs events sent, duplicates, SHA-256 recomputed by PostgreSQL for every row, every vault object re-hashed, Merkle seal + full chain verification. |


Environment: Docker Desktop, the dev stack running (`docker compose up -d`), commit as in section 3.
All commands run the harness inside the backend container; `scripts/bench.sh` also records host facts
(git SHA, Docker version, host OS/CPU/RAM) and samples `docker stats` for both containers every ~1–3 s.
Seed 1337; format mix fortinet,cef,leef,xml,json,syslog; size bands small,medium,large,xlarge; malformed 5 %.
The benchmark writes only to the `logforge_bench` database (created and migrated with `alembic upgrade head`
automatically), `/tmp/logforge-bench/…` inside the container, and new result directories.

```bash
# 1. standard 46-run matrix (load levels 1/100/1,000/10,000, variants, malformed sweep, size bands, HTTP)
bash scripts/bench.sh suite
# 2. completion plan: V0-V3 x 3 interleaved + HTTP 1/2/4/8 connections x batch 1/50/100, with integrity checks
bash scripts/bench.sh suite --plan completion
# 3. the 5-minute sustained run (30 s warm-up + 300 s steady state), HTTP, 4 connections, scheduler on
bash scripts/bench.sh sustained --mode http --variant V3_full --workers 4 --batch-size 1 --scheduler \
     --duration 300 --warmup-seconds 30 --window 10 --integrity --seed 1337
# 4. render this report (paths inside the container; then copy it to docs/)
bash scripts/bench.sh report --suite logforge_bench/results/<standard-suite> --suite logforge_bench/results/<completion-suite> \
     --sustained logforge_bench/results/<sustained-run>/result.json \
     --docker-stats logforge_bench/results/docker_stats-<...>.jsonl --out logforge_bench/results/phase8-step10-benchmark.md

# single runs (every option)
docker compose exec backend python -m logforge_bench run --mode inprocess --variant V3_full --events 10000 \
    --seed 1337 --format fortinet,cef,leef,xml,json,syslog --size small,medium,large,xlarge \
    --malformed-rate 0.05 --workers 1 --batch-size 1 --jobs --integrity
docker compose exec backend python -m logforge_bench run --mode http --variant V3_full --events 2000 --workers 4 --batch-size 50
docker compose exec backend python -m logforge_bench dataset --events 1000 --seed 1337
docker compose exec backend python -m logforge_bench suite --quick        # harness smoke test
```

No internet access is required. Nothing overwrites earlier results.

## D. Full 46-run standard suite

Suite `suite-20260928T135805Z`: **46 of 46 runs completed**, 0 failed. S/P/F/UR = success / partial / failed / under review (Phase 5 drift hold). Latency per event (batch runs: request ÷ batch).

| Run | Kind | Mode | Variant | Conns | Batch | Events | Events/s | p50 ms | p95 ms | p99 ms | S/P/F/UR | Errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-inproc-pipeline_only-1 | MEASURED | inprocess | pipeline_only | 1 | 1 | 1 | 10.97 | 91.13 | 91.13 | 91.13 | 1/0/0/0 | 0 |
| A-inproc-pipeline_only-100 | MEASURED | inprocess | pipeline_only | 1 | 1 | 100 | 2,446.80 | 0.18 | 1.45 | 2.03 | 97/2/1/0 | 0 |
| A-inproc-pipeline_only-1000 | MEASURED | inprocess | pipeline_only | 1 | 1 | 1,000 | 2,625.29 | 0.18 | 1.12 | 2.19 | 957/8/35/0 | 0 |
| A-inproc-pipeline_only-10000 | MEASURED | inprocess | pipeline_only | 1 | 1 | 10,000 | 1,623.26 | 0.27 | 2.19 | 4.25 | 9553/109/338/0 | 0 |
| B-inproc-v3_full-1 | MEASURED | inprocess | v3_full | 1 | 1 | 1 | 1.46 | 686.17 | 686.17 | 686.17 | 1/0/0/0 | 0 |
| B-inproc-v3_full-100 | MEASURED | inprocess | v3_full | 1 | 1 | 100 | 41.47 | 22.40 | 37.64 | 50.15 | 71/1/1/27 | 0 |
| B-inproc-v3_full-1000 | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 28.81 | 29.70 | 73.46 | 104.49 | 716/4/35/245 | 0 |
| B-inproc-v3_full-10000 | MEASURED | inprocess | v3_full | 1 | 1 | 10,000 | 36.82 | 22.64 | 60.12 | 87.01 | 7164/48/338/2450 | 0 |
| C-variant-pipeline_only-rep1 | MEASURED | inprocess | pipeline_only | 1 | 1 | 1,000 | 1,653.89 | 0.26 | 2.16 | 3.48 | 957/8/35/0 | 0 |
| C-variant-core_nodrift-rep1 | MEASURED | inprocess | core_nodrift | 1 | 1 | 1,000 | 48.89 | 18.62 | 40.00 | 52.29 | 957/8/35/0 | 0 |
| C-variant-v0_core-rep1 | MEASURED | inprocess | v0_core | 1 | 1 | 1,000 | 52.92 | 17.06 | 35.59 | 53.24 | 716/4/35/245 | 0 |
| C-variant-v1_phase7-rep1 | MEASURED | inprocess | v1_phase7 | 1 | 1 | 1,000 | 58.05 | 15.30 | 33.06 | 45.12 | 716/4/35/245 | 0 |
| C-variant-v3_full-rep1 | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 41.63 | 17.87 | 63.78 | 96.89 | 716/4/35/245 | 0 |
| C-variant-core_nodrift-rep2 | MEASURED | inprocess | core_nodrift | 1 | 1 | 1,000 | 41.23 | 19.71 | 54.13 | 73.05 | 957/8/35/0 | 0 |
| C-variant-v0_core-rep2 | MEASURED | inprocess | v0_core | 1 | 1 | 1,000 | 53.80 | 15.78 | 40.24 | 60.92 | 716/4/35/245 | 0 |
| C-variant-v1_phase7-rep2 | MEASURED | inprocess | v1_phase7 | 1 | 1 | 1,000 | 42.77 | 20.51 | 48.87 | 69.42 | 716/4/35/245 | 0 |
| C-variant-v3_full-rep2 | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 36.55 | 23.39 | 54.34 | 92.52 | 716/4/35/245 | 0 |
| C-variant-pipeline_only-rep2 | MEASURED | inprocess | pipeline_only | 1 | 1 | 1,000 | 1,492.97 | 0.30 | 2.54 | 3.97 | 957/8/35/0 | 0 |
| C-variant-v0_core-rep3 | MEASURED | inprocess | v0_core | 1 | 1 | 1,000 | 68.90 | 12.63 | 31.32 | 43.17 | 716/4/35/245 | 0 |
| C-variant-v1_phase7-rep3 | MEASURED | inprocess | v1_phase7 | 1 | 1 | 1,000 | 59.29 | 14.57 | 35.85 | 61.55 | 716/4/35/245 | 0 |
| C-variant-v3_full-rep3 | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 47.85 | 17.39 | 45.98 | 67.03 | 716/4/35/245 | 0 |
| C-variant-pipeline_only-rep3 | MEASURED | inprocess | pipeline_only | 1 | 1 | 1,000 | 2,014.56 | 0.23 | 1.65 | 2.64 | 957/8/35/0 | 0 |
| C-variant-core_nodrift-rep3 | MEASURED | inprocess | core_nodrift | 1 | 1 | 1,000 | 62.74 | 13.86 | 32.88 | 52.15 | 957/8/35/0 | 0 |
| D-malformed-0pct | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 32.91 | 25.09 | 65.57 | 99.57 | 748/0/0/252 | 0 |
| D-malformed-25pct | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 40.83 | 21.10 | 47.73 | 70.66 | 569/35/177/219 | 0 |
| D-malformed-50pct | MEASURED | inprocess | v3_full | 1 | 1 | 1,000 | 60.47 | 14.16 | 36.87 | 54.93 | 372/72/370/186 | 0 |
| E-size-small | MEASURED | inprocess | v3_full | 1 | 1 | 600 | 69.71 | 11.86 | 28.89 | 41.36 | 567/6/27/0 | 0 |
| E-size-medium | MEASURED | inprocess | v3_full | 1 | 1 | 600 | 49.74 | 16.17 | 40.51 | 61.61 | 572/5/23/0 | 0 |
| E-size-large | MEASURED | inprocess | v3_full | 1 | 1 | 600 | 36.89 | 22.95 | 50.12 | 71.27 | 569/8/23/0 | 0 |
| E-size-xlarge | MEASURED | inprocess | v3_full | 1 | 1 | 600 | 27.73 | 34.73 | 64.75 | 97.19 | 575/7/18/0 | 0 |
| F-inproc-v3_full-workers4 | MEASURED | inprocess | v3_full | 4 | 1 | 2,000 | 48.50 | 78.16 | 140.70 | 171.45 | 1426/11/70/493 | 0 |
| G-http-v3_full-1 | MEASURED | http | v3_full | 1 | 1 | 1 | 7.71 | 129.21 | 129.21 | 129.21 | 1/0/0/0 | 0 |
| G-http-v3_full-100 | MEASURED | http | v3_full | 1 | 1 | 100 | 50.95 | 17.88 | 32.51 | 47.79 | 71/1/1/27 | 0 |
| G-http-v3_full-1000 | MEASURED | http | v3_full | 1 | 1 | 1,000 | 48.53 | 17.20 | 42.85 | 61.71 | 716/4/35/245 | 0 |
| G-http-v3_full-10000 | MEASURED | http | v3_full | 4 | 1 | 10,000 | 34.30 | 103.42 | 231.56 | 291.28 | 7166/49/338/2447 | 0 |
| H-http-v3_full-workers2 | MEASURED | http | v3_full | 2 | 1 | 1,000 | 42.17 | 38.88 | 107.12 | 140.57 | 716/4/35/245 | 0 |
| H-http-v3_full-workers4 | MEASURED | http | v3_full | 4 | 1 | 2,000 | 30.37 | 114.26 | 246.24 | 294.85 | 1426/11/70/493 | 0 |
| H-http-v3_full-workers8 | MEASURED | http | v3_full | 8 | 1 | 4,000 | 28.22 | 248.11 | 557.15 | 715.54 | 2875/18/131/976 | 0 |
| I-http-batch100-workers1 | MEASURED | http | v3_full | 1 | 100 | 2,000 | 39.74 | 24.90 | 33.81 | 35.52 | 1426/11/70/493 | 0 |
| I-http-batch100-workers4 | MEASURED | http | v3_full | 4 | 100 | 2,000 | 36.91 | 107.53 | 132.01 | 132.65 | 1426/11/70/493 | 0 |
| J-http-variant-v0_core-rep1 | MEASURED | http | v0_core | 4 | 1 | 1,000 | 35.92 | 102.84 | 195.80 | 237.35 | 712/7/35/246 | 0 |
| J-http-variant-v1_phase7-rep1 | MEASURED | http | v1_phase7 | 4 | 1 | 1,000 | 47.20 | 73.35 | 175.07 | 230.75 | 717/5/35/243 | 0 |
| J-http-variant-v3_full-rep1 | MEASURED | http | v3_full | 4 | 1 | 1,000 | 37.21 | 86.32 | 218.48 | 270.17 | 716/4/35/245 | 0 |
| J-http-variant-v3_full-rep2 | MEASURED | http | v3_full | 4 | 1 | 1,000 | 20.98 | 182.73 | 301.44 | 349.25 | 717/4/35/244 | 0 |
| J-http-variant-v1_phase7-rep2 | MEASURED | http | v1_phase7 | 4 | 1 | 1,000 | 48.54 | 71.36 | 161.03 | 203.80 | 716/4/35/245 | 0 |
| J-http-variant-v0_core-rep2 | MEASURED | http | v0_core | 4 | 1 | 1,000 | 56.78 | 64.56 | 137.45 | 179.79 | 717/4/35/244 | 0 |

## E. Completion-suite results

Suite `suite-20260928T143154Z-completion`: **24 of 24 runs completed**, 0 failed. S/P/F/UR = success / partial / failed / under review (Phase 5 drift hold). Latency per event (batch runs: request ÷ batch).

| Run | Kind | Mode | Variant | Conns | Batch | Events | Events/s | p50 ms | p95 ms | p99 ms | S/P/F/UR | Errors | Integrity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K-V0_core-rep1 | MEASURED | inprocess | V0_core | 1 | 1 | 1,000 | 86.67 | 9.29 | 25.10 | 41.19 | 957/8/35/0 | 0 | PASS |
| K-V1_core_phase7-rep1 | MEASURED | inprocess | V1_core_phase7 | 1 | 1 | 1,000 | 62.73 | 12.68 | 34.47 | 51.85 | 957/8/35/0 | 0 | PASS |
| K-V2_core_drift-rep1 | MEASURED | inprocess | V2_core_drift | 1 | 1 | 1,000 | 52.43 | 16.40 | 39.37 | 60.40 | 716/4/35/245 | 0 | PASS |
| K-V3_full-rep1 | MEASURED | inprocess | V3_full | 1 | 1 | 1,000 | 33.80 | 26.21 | 59.36 | 76.74 | 716/4/35/245 | 0 | PASS |
| K-V1_core_phase7-rep2 | MEASURED | inprocess | V1_core_phase7 | 1 | 1 | 1,000 | 46.54 | 20.42 | 35.73 | 51.51 | 957/8/35/0 | 0 | PASS |
| K-V2_core_drift-rep2 | MEASURED | inprocess | V2_core_drift | 1 | 1 | 1,000 | 82.53 | 10.04 | 26.70 | 40.42 | 716/4/35/245 | 0 | PASS |
| K-V3_full-rep2 | MEASURED | inprocess | V3_full | 1 | 1 | 1,000 | 48.02 | 17.12 | 45.57 | 65.43 | 716/4/35/245 | 0 | PASS |
| K-V0_core-rep2 | MEASURED | inprocess | V0_core | 1 | 1 | 1,000 | 93.74 | 8.31 | 25.14 | 34.41 | 957/8/35/0 | 0 | PASS |
| K-V2_core_drift-rep3 | MEASURED | inprocess | V2_core_drift | 1 | 1 | 1,000 | 67.15 | 13.04 | 30.66 | 43.44 | 716/4/35/245 | 0 | PASS |
| K-V3_full-rep3 | MEASURED | inprocess | V3_full | 1 | 1 | 1,000 | 47.46 | 17.64 | 42.35 | 65.35 | 716/4/35/245 | 0 | PASS |
| K-V0_core-rep3 | MEASURED | inprocess | V0_core | 1 | 1 | 1,000 | 68.25 | 12.72 | 29.66 | 41.84 | 957/8/35/0 | 0 | PASS |
| K-V1_core_phase7-rep3 | MEASURED | inprocess | V1_core_phase7 | 1 | 1 | 1,000 | 34.26 | 25.73 | 56.27 | 78.93 | 957/8/35/0 | 0 | PASS |
| L-http-c1-b1 | MEASURED | http | V3_full | 1 | 1 | 2,000 | 33.96 | 25.40 | 58.97 | 85.82 | 1426/11/70/493 | 0 | PASS |
| L-http-c2-b1 | MEASURED | http | V3_full | 2 | 1 | 2,000 | 32.89 | 54.69 | 113.76 | 144.28 | 1426/11/70/493 | 0 | PASS |
| L-http-c4-b1 | MEASURED | http | V3_full | 4 | 1 | 2,000 | 38.85 | 91.22 | 199.88 | 248.89 | 1426/11/70/493 | 0 | PASS |
| L-http-c8-b1 | MEASURED | http | V3_full | 8 | 1 | 2,000 | 28.57 | 267.71 | 495.28 | 597.75 | 1429/10/70/491 | 0 | PASS |
| L-http-c1-b50 | MEASURED | http | V3_full | 1 | 50 | 2,000 | 37.63 | 26.29 | 38.85 | 39.41 | 1426/11/70/493 | 0 | PASS |
| L-http-c2-b50 | MEASURED | http | V3_full | 2 | 50 | 2,000 | 58.59 | 34.13 | 39.09 | 41.30 | 1426/11/70/493 | 0 | PASS |
| L-http-c4-b50 | MEASURED | http | V3_full | 4 | 50 | 2,000 | 37.33 | 105.57 | 145.76 | 155.76 | 1426/11/70/493 | 0 | PASS |
| L-http-c8-b50 | MEASURED | http | V3_full | 8 | 50 | 2,000 | 40.61 | 194.85 | 209.81 | 213.58 | 1422/14/70/494 | 0 | PASS |
| L-http-c1-b100 | MEASURED | http | V3_full | 1 | 100 | 2,000 | 44.15 | 21.31 | 28.16 | 34.13 | 1426/11/70/493 | 0 | PASS |
| L-http-c2-b100 | MEASURED | http | V3_full | 2 | 100 | 2,000 | 51.57 | 36.66 | 51.41 | 52.03 | 1426/11/70/493 | 0 | PASS |
| L-http-c4-b100 | MEASURED | http | V3_full | 4 | 100 | 2,000 | 51.54 | 75.56 | 86.09 | 86.71 | 1426/11/70/493 | 0 | PASS |
| L-http-c8-b100 | MEASURED | http | V3_full | 8 | 100 | 2,000 | 38.39 | 204.84 | 215.49 | 216.09 | 1426/11/70/493 | 0 | PASS |

## F. HTTP concurrency results

Completion plan `L-*`: 2,000 events per run, `V3_full` (default configuration), scheduler off, single runs. Server CPU = the uvicorn process (`/proc`, 100 % = one core busy); DB CPU = the `logforge-db` container from `docker stats` over the measurement window.

| Conns / batch | Kind | Events/s | Events/min | Req p50 ms | Req p95 ms | Req p99 ms | Scaling eff. vs 1 conn | Errors+timeouts | Server CPU % | Server RSS MB | DB CPU % | DB commits/s | Rows inserted/s | Mean active PG backends | Top PG activity (active) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c1 b1 | MEASURED | 33.96 | 2,037.40 | 25.40 | 58.97 | 85.82 | 1.00 | 0 | 57.80 | 117.94 | 23.90 | 105.90 | 301.20 | 0.18 | on CPU (no wait event) 86%; waiting Client:ClientRead 5% |
| c2 b1 | MEASURED | 32.89 | 1,973.70 | 54.69 | 113.76 | 144.28 | 0.48 | 0 | 96.40 | 118.82 | 36.10 | 102.40 | 291.10 | 0.27 | on CPU (no wait event) 73%; waiting IO:WALSync 21% |
| c4 b1 | MEASURED | 38.85 | 2,331.10 | 91.22 | 199.88 | 248.89 | 0.29 | 0 | 120.00 | 120.54 | 35.20 | 118.50 | 335.20 | 0.28 | on CPU (no wait event) 76%; waiting IO:WALSync 14% |
| c8 b1 | MEASURED | 28.57 | 1,714.10 | 267.71 | 495.28 | 597.75 | 0.11 | 0 | 136.20 | 123.72 | 32.00 | 89.00 | 249.50 | 0.27 | on CPU (no wait event) 63%; waiting IO:WALSync 24% |
| c1 b50 | MEASURED | 37.63 | 2,258.00 | 1,314.30 | 1,942.30 | 1,960.69 | 1.00 | 0 | 60.50 | 127.12 | 22.40 | 79.40 | 328.80 | 0.18 | on CPU (no wait event) 75%; waiting IO:WALSync 20% |
| c2 b50 | MEASURED | 58.59 | 3,515.50 | 1,706.61 | 1,954.71 | 2,051.95 | 0.78 | 0 | 93.20 | 130.96 | 34.30 | 121.40 | 507.20 | 0.16 | on CPU (no wait event) 64%; waiting Client:ClientRead 27% |
| c4 b50 | MEASURED | 37.33 | 2,239.50 | 5,278.35 | 7,287.97 | 7,719.81 | 0.25 | 0 | 120.50 | 139.26 | 34.90 | 78.60 | 324.30 | 0.24 | on CPU (no wait event) 69%; waiting IO:WALSync 15% |
| c8 b50 | MEASURED | 40.61 | 2,436.50 | 9,742.70 | 10,490.46 | 10,657.96 | 0.13 | 0 | 135.70 | 154.51 | 28.30 | 85.90 | 355.80 | 0.30 | on CPU (no wait event) 37%; waiting Lock:transactionid 30% |
| c1 b100 | MEASURED | 44.15 | 2,649.10 | 2,130.72 | 2,815.66 | 3,293.45 | 1.00 | 0 | 61.30 | 133.40 | 21.50 | 93.40 | 392.00 | 0.19 | on CPU (no wait event) 82%; waiting Client:ClientRead 12% |
| c2 b100 | MEASURED | 51.57 | 3,094.40 | 3,666.15 | 5,140.81 | 5,190.46 | 0.58 | 0 | 93.20 | 140.02 | 35.60 | 104.60 | 439.50 | 0.25 | on CPU (no wait event) 70%; waiting IO:WALSync 25% |
| c4 b100 | MEASURED | 51.54 | 3,092.50 | 7,556.15 | 8,609.47 | 8,658.35 | 0.29 | 0 | 118.90 | 155.71 | 31.90 | 105.80 | 445.10 | 0.24 | on CPU (no wait event) 63%; waiting IO:WALSync 26% |
| c8 b100 | MEASURED | 38.39 | 2,303.60 | 20,483.58 | 21,549.04 | 21,597.30 | 0.11 | 0 | 132.70 | 181.07 | 29.20 | 80.30 | 335.20 | 0.18 | on CPU (no wait event) 68%; waiting IO:WALSync 21% |

For batch runs the request latency covers the whole batch (50 or 100 events).

Scaling efficiency = (throughput at N ÷ throughput at 1) ÷ N, computed from the `L-*` runs (single runs each; the between-run variance in section P applies).

| Series | Connections N | Kind | Events/s at 1 | Events/s at N | Speed-up | Efficiency |
|---|---|---|---|---|---|---|
| batch 1 | 2 | MEASURED | 33.96 | 32.89 | 0.97× | 0.48 |
| batch 1 | 4 | MEASURED | 33.96 | 38.85 | 1.14× | 0.29 |
| batch 1 | 8 | MEASURED | 33.96 | 28.57 | 0.84× | 0.11 |
| batch 50 | 2 | MEASURED | 37.63 | 58.59 | 1.56× | 0.78 |
| batch 50 | 4 | MEASURED | 37.63 | 37.33 | 0.99× | 0.25 |
| batch 50 | 8 | MEASURED | 37.63 | 40.61 | 1.08× | 0.13 |
| batch 100 | 2 | MEASURED | 44.15 | 51.57 | 1.17× | 0.58 |
| batch 100 | 4 | MEASURED | 44.15 | 51.54 | 1.17× | 0.29 |
| batch 100 | 8 | MEASURED | 44.15 | 38.39 | 0.87× | 0.11 |

These are client connections to **one** API process and **one** PostgreSQL. No multi-replica scaling was measured.


Earlier standard-suite HTTP runs with several connections (batch 1, `v3_full`): `G-http-v3_full-10000` 4 conn, 10,000 events → 34.30 events/s, p95 231.555 ms; `H-http-v3_full-workers2` 2 conn, 1,000 events → 42.17 events/s, p95 107.123 ms; `H-http-v3_full-workers4` 4 conn, 2,000 events → 30.37 events/s, p95 246.241 ms; `H-http-v3_full-workers8` 8 conn, 4,000 events → 28.22 events/s, p95 557.149 ms (MEASURED).

## G. 5-minute sustained results

**Configuration:** HTTP, variant `V3_full`, 4 connections, batch 1, scheduler on (interval 60 s), seed 1337, requested warm-up 30 s + steady state 300 s, windows 10 s. Actual wall time 330.0 s (the last in-flight requests finish after the stop time). Single run.

| Phase | Kind | Events | Events/s | p50 ms | p95 ms | p99 ms | max ms | Errors |
|---|---|---|---|---|---|---|---|---|
| warm-up | MEASURED | 1,514 | 50.47 | 68.74 | 158.99 | 207.87 | 298.11 |  |
| steady state | MEASURED | 11,220 | 37.40 | 86.64 | 221.43 | 290.83 | 444.44 | 0 |

Peak steady window: 48.8 events/s (window 3); lowest: 17.0 events/s (window 26). HTTP: {"201": 12738}, timeouts 0, transport/HTTP errors 0.

Per-window evidence (MEASURED):

| Window | t (s) | Phase | Events | Events/s | p50 | p95 | p99 | Errors | Server CPU % | DB container CPU % | DB container MiB | Backend container MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0–10 | warmup | 498 | 49.80 | 64.14 | 168.04 | 216.24 | 0 | 120.30 | 38.60 | 227.10 | 931.10 |
| 1 | 10–20 | warmup | 473 | 47.30 | 74.04 | 165.00 | 237.72 | 0 | 121.90 | 38.90 | 227.10 | 936.20 |
| 2 | 20–30 | warmup | 543 | 54.30 | 66.89 | 137.50 | 187.56 | 0 | 122.30 | 41.20 | 227.60 | 942.00 |
| 3 | 30–40 | steady | 488 | 48.80 | 70.50 | 157.72 | 202.42 | 0 | 121.80 | 40.00 | 227.50 | 947.80 |
| 4 | 40–50 | steady | 381 | 38.10 | 90.14 | 202.13 | 263.83 | 0 | 122.50 | 40.50 | 227.70 | 951.30 |
| 5 | 50–60 | steady | 383 | 38.30 | 82.43 | 217.31 | 270.60 | 0 | 122.90 | 42.80 | 229.70 | 969.90 |
| 6 | 60–70 | steady | 356 | 35.60 | 98.69 | 225.33 | 312.54 | 0 | 125.30 | 39.20 | 230.60 | 975.10 |
| 7 | 70–80 | steady | 382 | 38.20 | 89.43 | 198.49 | 242.48 | 0 | 121.70 | 39.10 | 231.30 | 979.90 |
| 8 | 80–90 | steady | 421 | 42.10 | 75.46 | 203.46 | 274.53 | 0 | 122.00 | 39.00 | 231.70 | 992.90 |
| 9 | 90–100 | steady | 412 | 41.20 | 82.09 | 179.17 | 226.91 | 0 | 121.60 | 41.10 | 231.90 | 988.00 |
| 10 | 100–110 | steady | 352 | 35.20 | 103.04 | 205.70 | 244.76 | 0 | 122.80 | 41.50 | 232.00 | 1,004.00 |
| 11 | 110–120 | steady | 388 | 38.80 | 86.33 | 208.97 | 235.72 | 0 | 122.10 | 41.70 | 232.30 | 995.70 |
| 12 | 120–130 | steady | 273 | 27.30 | 125.76 | 277.85 | 355.20 | 0 | 126.20 | 39.20 | 232.40 | 1,010.00 |
| 13 | 130–140 | steady | 417 | 41.70 | 73.81 | 211.65 | 257.28 | 0 | 124.00 | 41.00 | 234.50 | 1,015.00 |
| 14 | 140–150 | steady | 463 | 46.30 | 75.47 | 169.75 | 197.88 | 0 | 121.60 | 37.80 | 232.70 | 1,018.00 |
| 15 | 150–160 | steady | 418 | 41.80 | 80.54 | 180.46 | 232.07 | 0 | 123.00 | 37.30 | 233.10 | 1,022.00 |
| 16 | 160–170 | steady | 369 | 36.90 | 96.21 | 200.46 | 256.58 | 0 | 122.80 | 38.80 | 233.40 | 1,026.00 |
| 17 | 170–180 | steady | 465 | 46.50 | 76.87 | 159.28 | 198.27 | 0 | 121.20 | 39.50 | 233.60 | 1,036.30 |
| 18 | 180–190 | steady | 442 | 44.20 | 77.31 | 182.67 | 212.96 | 0 | 121.80 | 39.60 | 234.00 | 1,034.20 |
| 19 | 190–200 | steady | 431 | 43.10 | 78.47 | 183.07 | 234.72 | 0 | 122.00 | 42.00 | 235.80 | 1,038.30 |
| 20 | 200–210 | steady | 419 | 41.90 | 75.78 | 194.80 | 269.74 | 0 | 122.30 | 40.20 | 233.60 | 1,042.40 |
| 21 | 210–220 | steady | 404 | 40.40 | 84.24 | 201.30 | 239.19 | 0 | 122.70 | 37.90 | 234.30 | 1,046.50 |
| 22 | 220–230 | steady | 455 | 45.50 | 73.66 | 167.08 | 223.59 | 0 | 121.80 | 39.30 | 234.30 | 1,050.60 |
| 23 | 230–240 | steady | 406 | 40.60 | 81.00 | 182.48 | 229.28 | 0 | 122.50 | 39.00 | 234.70 | 1,054.70 |
| 24 | 240–250 | steady | 307 | 30.70 | 108.45 | 237.21 | 336.24 | 0 | 123.30 | 43.40 | 241.40 | 1,058.80 |
| 25 | 250–260 | steady | 228 | 22.80 | 155.82 | 315.56 | 364.29 | 0 | 123.30 | 35.60 | 234.50 | 1,059.80 |
| 26 | 260–270 | steady | 170 | 17.00 | 223.93 | 348.52 | 402.12 | 0 | 125.00 | 46.50 | 235.20 | 1,061.90 |
| 27 | 270–280 | steady | 230 | 23.00 | 161.04 | 324.94 | 351.14 | 0 | 123.40 | 36.40 | 235.00 | 1,063.90 |
| 28 | 280–290 | steady | 280 | 28.00 | 118.39 | 283.95 | 343.99 | 0 | 123.50 | 40.30 | 235.10 | 1,067.00 |
| 29 | 290–300 | steady | 350 | 35.00 | 101.95 | 218.08 | 277.36 | 0 | 122.30 | 39.30 | 235.60 | 1,070.10 |
| 30 | 300–310 | steady | 335 | 33.50 | 99.27 | 241.65 | 277.65 | 0 | 122.60 | 38.90 | 235.80 | 1,073.20 |
| 31 | 310–320 | steady | 363 | 36.30 | 98.45 | 201.44 | 231.45 | 0 | 123.30 | 42.10 | 236.90 | 1,089.50 |
| 32 | 320–330 | steady | 432 | 43.20 | 75.58 | 192.55 | 235.91 | 0 | 121.50 | 26.20 | 237.50 | 1,089.50 |
| 33 | 330–340 | partial_final_window | 4 | 0.40 | 99.43 | 113.27 | 115.08 | 0 | 24.50 | 2.30 | 238.30 | 1,097.70 |

Degradation checks (MEASURED; thresholds fixed in `runner.THRESHOLDS` before the run):

| Check | Kind | Value | Threshold | Crossed? |
|---|---|---|---|---|
| throughput last-3 ÷ first-3 steady windows | MEASURED | 0.90 | < 0.8 | no |
| throughput slope (events/s per min) | MEASURED | -1.96 | informational |  |
| p95 last-3 ÷ first-3 | MEASURED | 1.10 | > 1.5 | no |
| p95 slope (ms per min) | MEASURED | 12.34 | informational |  |
| errors (total, trend) | MEASURED | 0 | any and increasing | no |
| server_rss_mb: growth MB (slope MB/min) | MEASURED | 26.62 (4.399) | > 50 MB or > 25 % | no |
| harness_rss_mb: growth MB (slope MB/min) | MEASURED | 28.88 (2.877) | > 50 MB or > 25 % | YES |
| cgroup_mb: growth MB (slope MB/min) | MEASURED | 214.69 (39.351) | > 50 MB or > 25 % | YES |
| PostgreSQL container memory first → last (MiB) | MEASURED | 226.8 → 237.8 (max 241.4) | informational |  |

**Harness verdict (application thresholds: throughput, p95, errors, API-server RSS):** NO DEGRADATION DETECTED within the stated thresholds over 300 s of steady state at this load; this does not demonstrate behavior over longer runs or at larger table sizes

- **Memory thresholds crossed outside the API server:** `harness_rss_mb`, `cgroup_mb` (table above). The harness judges only the API process; these series include the load generator, which keeps every per-event record in memory by design, and (for the cgroup) the page cache of the raw-vault files written during the run. That this explains the growth is **LIKELY**, not proven.
- **API-server RSS grew 26.62 MB over the steady state (4.399 MB/min)**, below the threshold. Whether this levels off (caches warming) or keeps growing cannot be told from 5 minutes: **UNKNOWN** — a multi-hour run is needed before claiming stable memory.
- **Throughput dip:** 3 steady window(s) fell below 60 % of the median (38.5 events/s): windows 25 (22.8), 26 (17.0), 27 (23.0), then recovered. The degradation ratio compares the first and last three windows, so it does not flag a mid-run dip. Its cause is **not isolated** (see the server and DB CPU columns for the same windows).

**Degradation result: PASS** on the stated application thresholds; memory thresholds were crossed by the load generator / container series, as described above.

Integrity after the run: events 12,738 of 12,738 expected (missing 0, extra 0); duplicate raw-hash groups 6; SHA-256 mismatches 0; vault objects re-hashed OK 12738 / mismatched 0 / missing 0; Merkle chain valid yes (12738 events sealed in 2 batches); statuses {"FAILED": 429, "PARTIAL": 72, "SUCCESS": 9117, "UNDER_REVIEW": 3120}; **all checks passed: yes**.

Events table after the run: 12,738 rows. This shows behavior while the table grows from empty to that size, not at production table sizes.

## H. In-process vs HTTP comparison

Same configuration (`v3_full` / `V3_full`), same dataset and seed. In-process = no HTTP, no JSON request/response work.

| Comparison | Kind | In-process ev/s | HTTP ev/s | In-process p50 ms | HTTP p50 ms | In-process p95 ms | HTTP p95 ms |
|---|---|---|---|---|---|---|---|
| standard suite, 1,000 events, 1 thread vs 1 connection | MEASURED | 28.81 | 48.53 | 29.70 | 17.20 | 73.46 | 42.85 |
| standard suite, 100 events | MEASURED | 41.47 | 50.95 | 22.40 | 17.88 | 37.64 | 32.51 |
| completion plan: `K-V3_full-*` (mean of 3 runs) vs `L-http-c1-b1` | MEASURED | 43.09 | 33.96 | 20.32 | 25.40 | 49.09 | 58.97 |

The two paths share the same per-event database work, and the between-run variance (section P) is of the same order as any difference here, so the HTTP overhead itself is not isolated (UNKNOWN).

## I. V0 / V1 / V2 / V3 overhead comparison

Completion plan `K-*`: in-process, 1 thread, 1,000 events, 100 warm-up, 3 interleaved repetitions with rotated order.

| Configuration | Kind | Runs | Events/s per run | Events/s mean | Mean ms per run | Mean ms (avg) | p95 ms (avg) | p99 ms (avg) | `pipeline` ms/event per run (machine-speed check) |
|---|---|---|---|---|---|---|---|---|---|
| V0 core (drift off, Phase 7 vault off, Phase 8 hooks off) | MEASURED | 3 | 86.67, 93.74, 68.25 | 82.89 | 11.474, 10.604, 14.570 | 12.22 | 26.63 | 39.15 | 0.678, 0.679, 0.894 |
| V1 core + Phase 7 evidence (cold vault on; drift off; Phase 8 off) | MEASURED | 3 | 62.73, 46.54, 34.26 | 47.84 | 15.867, 21.395, 29.068 | 22.11 | 42.16 | 60.76 | 0.721, 1.005, 1.488 |
| V2 core + Phase 5 drift (vault off; Phase 8 off) | MEASURED | 3 | 52.43, 82.53, 67.15 | 67.37 | 18.980, 12.054, 14.819 | 15.28 | 32.24 | 48.09 | 0.974, 0.661, 0.752 |
| V3 full default configuration (drift + vault + Phase 8 hooks) | MEASURED | 3 | 33.80, 48.02, 47.46 | 43.09 | 29.492, 20.754, 20.996 | 23.75 | 49.09 | 69.18 | 1.044, 0.724, 0.701 |

Differences against V0 (positive latency Δ = slower). A percentage is given only when the per-run ranges do not overlap; even then it is an association with the switched feature on a shared laptop, not proof of causality:

| Configuration | Kind | Δ mean ms/event | Δ events/s | Δ % | Consistency |
|---|---|---|---|---|---|
| V1 core + Phase 7 evidence (cold vault on; drift off; Phase 8 off) | MEASURED (difference of means) | +9.894 | -35.04 | +81.0% | ranges do not overlap across repetitions |
| V2 core + Phase 5 drift (vault off; Phase 8 off) | MEASURED (difference of means) | +3.068 | -15.52 | not stated (ranges overlap) | ranges overlap: within run-to-run spread, not a demonstrated difference |
| V3 full default configuration (drift + vault + Phase 8 hooks) | MEASURED (difference of means) | +11.531 | -39.79 | +94.4% | ranges do not overlap across repetitions |

Per-stage cost inside the default configuration (`B-inproc-v3_full-10000`, mean event latency 27.07 ms). Stage timing measures each feature's code directly, so it is not affected by the between-run variance that hides the V0–V3 differences:

| Stage | Kind | Calls | ms/event | p95 ms/call | Share of mean latency |
|---|---|---|---|---|---|
| `phase7_persist_total` | MEASURED | 10,000 | 13.14 | 28.90 | 48.6% |
| `phase8_persist_hooks_total` | MEASURED | 10,000 | 7.40 | 17.43 | 27.3% |
| `phase7_raw_archive` | MEASURED | 10,000 | 4.64 | 10.00 | 17.1% |
| `event_insert_commit_refresh` | MEASURED | 10,000 | 4.58 | 10.85 | 16.9% |
| `hook:compact_lineage` | MEASURED | 10,000 | 4.47 | 10.77 | 16.5% |
| `phase5_drift` | MEASURED | 10,000 | 2.63 | 6.81 | 9.7% |
| `adapter_registry_lookup` | MEASURED | 10,000 | 2.18 | 5.33 | 8.0% |
| `phase7_overflow_write` | MEASURED | 4,848 | 1.10 | 5.08 | 4.1% |
| `pipeline` | MEASURED | 10,000 | 0.91 | 3.02 | 3.4% |
| `phase7_spill_prepare` | MEASURED | 10,000 | 0.61 | 2.24 | 2.3% |
| `hook:event_revisions` | MEASURED | 10,000 | 0.01 | 0.02 | 0.0% |

Nesting: `phase7_persist_total` contains `phase7_overflow_write`, `phase7_raw_archive` and `phase8_persist_hooks_total` (which contains the `hook:*` rows). Phase 7 work that runs in every persisting configuration: `phase7_spill_prepare`, `phase7_overflow_write` and the `event_raw_storage` row.


Off-ingest-path work (never per event, so no per-event overhead exists to compute), measured as jobs over the 10,050 events of `B-inproc-v3_full-10000`:

| Job | Kind | Duration ms | Detail |
|---|---|---|---|
| Phase 8 statistical/semantic drift ("V2 drift" job, persist=false) | MEASURED | 293.38 | 9,710 rows, 6/6 sources, 2 findings, 0.0302 ms/row |
| Shadow validation `fortinet` (identical candidate) | MEASURED | 961.92 | 107 sampled events, 642 pipeline runs, 8.99 ms/sampled event, verdict REVIEW_REQUIRED (INSUFFICIENT_COVERAGE) |
| Shadow validation `paloalto_cef` (identical candidate) | MEASURED | 1,204.26 | 109 sampled events, 654 pipeline runs, 11.048 ms/sampled event, verdict REVIEW_REQUIRED (INSUFFICIENT_COVERAGE) |
| Compact lineage storage (existing Phase 8 measurement) | MEASURED | 2,303.95 | row 80 B vs detailed lineage as JSONB 20241.12 B (ratio 253.01) |
| Merkle sealing (Phase 7 scheduler work) | MEASURED | 579.43 | 10,050 events, 2 batches, 0.0577 ms/event |

Earlier variant block (`C-*`, standard suite; `v0_core` = core + drift, `v1_phase7` = core + drift + vault, `v3_full` = all):

| Variant | Kind | Runs | Events/s per run | Mean ms per run | Mean ms (avg) |
|---|---|---|---|---|---|
| `pipeline_only` | MEASURED | 3 | 1,653.89, 1,492.97, 2,014.56 | 0.582, 0.644, 0.479 | 0.57 |
| `core_nodrift` | MEASURED | 3 | 48.89, 41.23, 62.74 | 20.348, 24.129, 15.853 | 20.11 |
| `v0_core` | MEASURED | 3 | 52.92, 53.80, 68.90 | 18.806, 18.502, 14.441 | 17.25 |
| `v1_phase7` | MEASURED | 3 | 58.05, 42.77, 59.29 | 17.148, 23.281, 16.787 | 19.07 |
| `v3_full` | MEASURED | 3 | 41.63, 36.55, 47.85 | 23.929, 27.263, 20.823 | 24.00 |

## J. PostgreSQL / WAL evidence

Collected for every completion-plan run and the sustained run (the standard-suite runs predate this instrumentation; their `pg_stat_database` deltas are in each run's `result.json`).

| Run | Kind | Commits/s | Commits/event | Rows ins/s | Rows ins/event | WAL B/event | WAL syncs/event | WAL MB/s | WAL fsync ms (avg) | WAL fsync ms/event | Checkpoints timed | Checkpoints req | Backend fsyncs | Max client conns | Mean active backends | DB CPU % mean | DB CPU % max | DB MiB max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K-V0_core-rep1 | MEASURED | 177.60 | 2.05 | 648.40 | 7.48 | 11,942.50 | 1.03 | 1.03 | 1.18 | 1.21 | 0.00 | 0.00 | 0.00 | 1 | 0.29 | 19.50 | 26.70 | 210.10 |
| K-V3_full-rep1 | MEASURED | 71.80 | 2.12 | 300.60 | 8.89 | 12,799.90 | 1.10 | 0.43 | 1.25 | 1.38 | 0.00 | 0.00 | 0.00 | 1 | 0.15 | 23.60 | 34.00 | 213.10 |
| K-V3_full-rep2 | MEASURED | 101.40 | 2.11 | 427.60 | 8.90 | 12,684.70 | 1.07 | 0.61 | 0.91 | 0.97 | 0.00 | 0.00 | 0.00 | 1 | 0.14 | 18.90 | 24.10 | 213.80 |
| K-V0_core-rep2 | MEASURED | 197.60 | 2.11 | 715.60 | 7.63 | 11,970.60 | 1.05 | 1.12 | 1.08 | 1.14 | 0.00 | 0.00 | 0.00 | 1 | 0.67 | 24.70 | 25.90 | 214.30 |
| K-V3_full-rep3 | MEASURED | 99.60 | 2.10 | 420.00 | 8.85 | 12,612.00 | 1.07 | 0.60 | 0.88 | 0.94 | 0.00 | 0.00 | 0.00 | 1 | 0.14 | 19.90 | 24.80 | 217.40 |
| K-V0_core-rep3 | MEASURED | 143.00 | 2.10 | 516.90 | 7.57 | 11,881.30 | 1.05 | 0.81 | 1.52 | 1.59 | 0.00 | 0.00 | 0.00 | 1 | 0.52 | 21.40 | 26.80 | 215.20 |
| L-http-c1-b1 | MEASURED | 105.90 | 3.12 | 301.20 | 8.87 | 12,830.10 | 1.07 | 0.44 | 1.06 | 1.14 | 0.00 | 0.00 | 0.00 | 2 | 0.18 | 23.90 | 32.30 | 217.90 |
| L-http-c2-b1 | MEASURED | 102.40 | 3.11 | 291.10 | 8.85 | 12,760.10 | 1.09 | 0.42 | 1.61 | 1.75 | 0.00 | 0.00 | 0.00 | 3 | 0.27 | 36.10 | 43.60 | 220.70 |
| L-http-c4-b1 | MEASURED | 118.50 | 3.05 | 335.20 | 8.63 | 12,568.20 | 1.04 | 0.49 | 1.62 | 1.68 | 0.00 | 0.00 | 0.00 | 5 | 0.28 | 35.20 | 41.20 | 227.50 |
| L-http-c8-b1 | MEASURED | 89.00 | 3.12 | 249.50 | 8.73 | 12,638.70 | 1.09 | 0.36 | 1.79 | 1.95 | 0.00 | 0.00 | 0.00 | 9 | 0.27 | 32.00 | 35.90 | 240.20 |
| L-http-c1-b50 | MEASURED | 79.40 | 2.11 | 328.80 | 8.74 | 12,624.80 | 1.06 | 0.47 | 1.09 | 1.16 | 0.00 | 0.00 | 0.00 | 2 | 0.18 | 22.40 | 33.40 | 221.30 |
| L-http-c2-b50 | MEASURED | 121.40 | 2.07 | 507.20 | 8.66 | 12,593.50 | 1.04 | 0.74 | 1.13 | 1.18 | 0.00 | 0.00 | 0.00 | 3 | 0.16 | 34.30 | 40.50 | 224.90 |
| L-http-c4-b50 | MEASURED | 78.60 | 2.11 | 324.30 | 8.69 | 13,056.70 | 1.06 | 0.49 | 1.83 | 1.94 | 1.00 | 0.00 | 0.00 | 5 | 0.24 | 34.90 | 41.80 | 234.10 |
| L-http-c8-b50 | MEASURED | 85.90 | 2.12 | 355.80 | 8.76 | 12,734.30 | 1.07 | 0.52 | 1.51 | 1.62 | 0.00 | 0.00 | 0.00 | 9 | 0.30 | 28.30 | 36.20 | 246.90 |
| L-http-c1-b100 | MEASURED | 93.40 | 2.12 | 392.00 | 8.88 | 12,884.60 | 1.07 | 0.57 | 0.92 | 0.98 | 0.00 | 0.00 | 0.00 | 2 | 0.19 | 21.50 | 27.60 | 225.20 |
| L-http-c2-b100 | MEASURED | 104.60 | 2.03 | 439.50 | 8.52 | 12,309.40 | 1.03 | 0.64 | 1.29 | 1.33 | 0.00 | 0.00 | 0.00 | 3 | 0.25 | 35.60 | 47.70 | 229.00 |
| L-http-c4-b100 | MEASURED | 105.80 | 2.05 | 445.10 | 8.63 | 12,490.10 | 1.04 | 0.64 | 1.36 | 1.42 | 0.00 | 0.00 | 0.00 | 5 | 0.24 | 31.90 | 40.80 | 234.10 |
| L-http-c8-b100 | MEASURED | 80.30 | 2.09 | 335.20 | 8.73 | 13,327.80 | 1.07 | 0.51 | 1.60 | 1.71 | 1.00 | 0.00 | 0.00 | 9 | 0.18 | 29.20 | 36.60 | 249.30 |
| sustained | MEASURED | 121.10 | 3.14 | 350.10 | 9.07 | 12,964.00 | 1.05 | 0.50 | 1.56 | 1.64 | 1.00 | 0.00 | 0.00 | 5 | 0.32 | 39.20 | 57.30 | 241.40 |

`xact_commit` includes read-only transactions. WAL and checkpoint counters are **cluster-wide** (the dev stack's database is on the same server, idle apart from its 60 s scheduler tick). WAL fsync time comes from `pg_stat_wal.wal_sync_time`, recorded only by sessions with `track_wal_io_timing` on — set for the `logforge_bench` database only, so it times the benchmark's own WAL flushes; an empty cell means it was not recorded for that run.

Where PostgreSQL client backends spent their time, all instrumented runs pooled (9,412 backend-samples at 0.5 s, MEASURED):

| State / wait event | Kind | Backend-samples | Share |
|---|---|---|---|
| idle in transaction (Client:ClientRead) | MEASURED | 5,668 | 60.2% |
| idle | MEASURED | 3,056 | 32.5% |
| active: on CPU (no wait event) | MEASURED | 388 | 4.1% |
| idle in transaction | MEASURED | 134 | 1.4% |
| active: waiting IO:WALSync | MEASURED | 104 | 1.1% |
| active: waiting Client:ClientRead | MEASURED | 38 | 0.4% |
| active: waiting Lock:transactionid | MEASURED | 12 | 0.1% |
| active: waiting LWLock:WALWrite | MEASURED | 5 | 0.1% |
| active: waiting IO:DataFileExtend | MEASURED | 2 | 0.0% |
| idle in transaction (IO:WALSync) | MEASURED | 2 | 0.0% |
| idle in transaction (IO:WALWrite) | MEASURED | 2 | 0.0% |
| active: waiting IO:WALWrite | MEASURED | 1 | 0.0% |

`idle` = a connection is open but the application is not using it (the application is working, or waiting on something other than PostgreSQL). `active: on CPU` = executing. `IO:WALSync` / `LWLock:WALWrite` = waiting for commit durability.

## K. CPU / RAM / resource behavior

CPU % = CPU seconds ÷ wall seconds × 100 (100 % = one core). In in-process runs the harness *is* the application; in HTTP runs the harness is the load generator and *server* is the API process. Docker columns come from the host `docker stats` series inside each run's measurement window (standard-suite runs: whole run window).

| Run | Kind | Mode | Conns/threads | Events/s | Harness CPU % | Server CPU % | Harness RSS MB | Server RSS MB | Backend cgroup MB | DB container CPU % | DB container MiB | Backend container CPU % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-inproc-pipeline_only-100 | MEASURED | inprocess | 1 | 2,446.80 | 105.00 | n/a (in-process) | NOT MEASURED | n/a (in-process) | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| A-inproc-pipeline_only-1000 | MEASURED | inprocess | 1 | 2,625.29 | 100.30 | n/a (in-process) | NOT MEASURED | n/a (in-process) | NOT MEASURED | 0.00 | 128.90 | 61.90 |
| A-inproc-pipeline_only-10000 | MEASURED | inprocess | 1 | 1,623.26 | 100.00 | n/a (in-process) | 131.03 | n/a (in-process) | 449.49 | 0.10 | 129.40 | 55.90 |
| B-inproc-v3_full-100 | MEASURED | inprocess | 1 | 41.47 | 61.40 | n/a (in-process) | 131.25 | n/a (in-process) | 452.07 | 34.60 | 131.10 | 75.10 |
| B-inproc-v3_full-1000 | MEASURED | inprocess | 1 | 28.81 | 64.30 | n/a (in-process) | 131.25 | n/a (in-process) | 473.27 | 23.50 | 132.90 | 77.90 |
| B-inproc-v3_full-10000 | MEASURED | inprocess | 1 | 36.82 | 66.20 | n/a (in-process) | 140.68 | n/a (in-process) | 608.00 | 22.00 | 199.60 | 73.80 |
| C-variant-pipeline_only-rep1 | MEASURED | inprocess | 1 | 1,653.89 | 99.00 | n/a (in-process) | 154.35 | n/a (in-process) | 614.30 | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| C-variant-core_nodrift-rep1 | MEASURED | inprocess | 1 | 48.89 | 66.50 | n/a (in-process) | 154.35 | n/a (in-process) | 624.68 | 15.20 | 198.50 | 50.10 |
| C-variant-v0_core-rep1 | MEASURED | inprocess | 1 | 52.92 | 67.00 | n/a (in-process) | 154.35 | n/a (in-process) | 614.85 | 22.80 | 199.40 | 73.70 |
| C-variant-v1_phase7-rep1 | MEASURED | inprocess | 1 | 58.05 | 68.50 | n/a (in-process) | 154.35 | n/a (in-process) | 615.13 | 18.20 | 200.40 | 51.60 |
| C-variant-v3_full-rep1 | MEASURED | inprocess | 1 | 41.63 | 69.50 | n/a (in-process) | 154.35 | n/a (in-process) | 615.13 | 25.00 | 201.60 | 65.80 |
| C-variant-core_nodrift-rep2 | MEASURED | inprocess | 1 | 41.23 | 67.00 | n/a (in-process) | 154.35 | n/a (in-process) | 615.39 | 29.50 | 201.90 | 72.10 |
| C-variant-v0_core-rep2 | MEASURED | inprocess | 1 | 53.80 | 67.80 | n/a (in-process) | 154.35 | n/a (in-process) | 614.88 | 20.10 | 202.30 | 78.60 |
| C-variant-v1_phase7-rep2 | MEASURED | inprocess | 1 | 42.77 | 67.50 | n/a (in-process) | 154.35 | n/a (in-process) | 625.07 | 22.30 | 203.40 | 87.50 |
| C-variant-v3_full-rep2 | MEASURED | inprocess | 1 | 36.55 | 69.00 | n/a (in-process) | 154.35 | n/a (in-process) | 628.14 | 19.10 | 203.90 | 71.40 |
| C-variant-pipeline_only-rep2 | MEASURED | inprocess | 1 | 1,492.97 | 100.40 | n/a (in-process) | 154.35 | n/a (in-process) | 615.15 | NOT MEASURED | NOT MEASURED | NOT MEASURED |
| C-variant-v0_core-rep3 | MEASURED | inprocess | 1 | 68.90 | 67.50 | n/a (in-process) | 154.35 | n/a (in-process) | 615.13 | 25.60 | 205.50 | 80.70 |
| C-variant-v1_phase7-rep3 | MEASURED | inprocess | 1 | 59.29 | 68.00 | n/a (in-process) | 154.35 | n/a (in-process) | 615.14 | 22.90 | 205.50 | 81.90 |
| C-variant-v3_full-rep3 | MEASURED | inprocess | 1 | 47.85 | 67.60 | n/a (in-process) | 154.35 | n/a (in-process) | 617.64 | 23.00 | 205.90 | 72.00 |
| C-variant-pipeline_only-rep3 | MEASURED | inprocess | 1 | 2,014.56 | 99.70 | n/a (in-process) | 154.35 | n/a (in-process) | 614.86 | 29.40 | 206.00 | 50.10 |
| C-variant-core_nodrift-rep3 | MEASURED | inprocess | 1 | 62.74 | 66.70 | n/a (in-process) | 154.35 | n/a (in-process) | 624.53 | 22.30 | 206.70 | 72.80 |
| D-malformed-0pct | MEASURED | inprocess | 1 | 32.91 | 68.60 | n/a (in-process) | 154.35 | n/a (in-process) | 626.73 | 27.10 | 206.90 | 80.20 |
| D-malformed-25pct | MEASURED | inprocess | 1 | 40.83 | 68.00 | n/a (in-process) | 154.35 | n/a (in-process) | 626.80 | 14.30 | 205.20 | 52.10 |
| D-malformed-50pct | MEASURED | inprocess | 1 | 60.47 | 67.70 | n/a (in-process) | 154.35 | n/a (in-process) | 628.78 | 21.10 | 205.60 | 72.70 |
| E-size-small | MEASURED | inprocess | 1 | 69.71 | 62.20 | n/a (in-process) | 154.35 | n/a (in-process) | 626.26 | 19.50 | 205.30 | 67.80 |
| E-size-medium | MEASURED | inprocess | 1 | 49.74 | 61.50 | n/a (in-process) | 154.37 | n/a (in-process) | 633.99 | 20.00 | 205.90 | 69.50 |
| E-size-large | MEASURED | inprocess | 1 | 36.89 | 63.90 | n/a (in-process) | 154.37 | n/a (in-process) | 643.21 | 23.10 | 206.50 | 74.80 |
| E-size-xlarge | MEASURED | inprocess | 1 | 27.73 | 64.50 | n/a (in-process) | 154.37 | n/a (in-process) | 652.77 | 31.10 | 214.40 | 64.20 |
| F-inproc-v3_full-workers4 | MEASURED | inprocess | 4 | 48.50 | 120.00 | n/a (in-process) | 157.25 | n/a (in-process) | 669.76 | 35.60 | 217.70 | 127.20 |
| G-http-v3_full-100 | MEASURED | http | 1 | 50.95 | 5.90 | 59.60 | 157.25 | 116.71 | 758.40 | 14.10 | 219.30 | 36.50 |
| G-http-v3_full-1000 | MEASURED | http | 1 | 48.53 | 4.80 | 61.70 | 157.25 | 118.43 | 751.04 | 25.70 | 220.40 | 64.50 |
| G-http-v3_full-10000 | MEASURED | http | 4 | 34.30 | 5.50 | 120.20 | 158.00 | 120.43 | 766.50 | 40.10 | 243.00 | 137.80 |
| H-http-v3_full-workers2 | MEASURED | http | 2 | 42.17 | 5.50 | 98.50 | 158.12 | 118.85 | 765.01 | 29.80 | 223.20 | 96.60 |
| H-http-v3_full-workers4 | MEASURED | http | 4 | 30.37 | 5.20 | 121.20 | 158.25 | 120.84 | 768.18 | 36.60 | 228.30 | 141.90 |
| H-http-v3_full-workers8 | MEASURED | http | 8 | 28.22 | 4.20 | 135.90 | 160.12 | 123.86 | 773.58 | 29.30 | 242.40 | 156.10 |
| I-http-batch100-workers1 | MEASURED | http | 1 | 39.74 | 2.70 | 65.50 | 172.93 | 133.02 | 779.77 | 22.90 | 221.60 | 80.40 |
| I-http-batch100-workers4 | MEASURED | http | 4 | 36.91 | 3.30 | 119.10 | 203.58 | 156.38 | 844.97 | 28.40 | 234.00 | 111.80 |
| J-http-variant-v0_core-rep1 | MEASURED | http | 4 | 35.92 | 6.30 | 119.10 | 172.01 | 119.09 | 776.87 | 25.30 | 230.90 | 91.70 |
| J-http-variant-v1_phase7-rep1 | MEASURED | http | 4 | 47.20 | 6.10 | 118.80 | 172.01 | 120.46 | 771.25 | 28.20 | 230.80 | 120.40 |
| J-http-variant-v3_full-rep1 | MEASURED | http | 4 | 37.21 | 4.90 | 120.40 | 172.01 | 120.50 | 769.83 | 29.40 | 232.20 | 133.80 |
| J-http-variant-v3_full-rep2 | MEASURED | http | 4 | 20.98 | 4.20 | 122.80 | 172.01 | 120.54 | 779.27 | 31.00 | 229.80 | 157.20 |
| J-http-variant-v1_phase7-rep2 | MEASURED | http | 4 | 48.54 | 7.70 | 117.10 | 172.01 | 120.11 | 769.08 | 28.70 | 229.90 | 112.90 |
| J-http-variant-v0_core-rep2 | MEASURED | http | 4 | 56.78 | 7.50 | 116.30 | 172.01 | 119.78 | 776.71 | 27.30 | 229.70 | 106.90 |
| K-V0_core-rep1 | MEASURED | inprocess | 1 | 86.67 | 66.20 | n/a (in-process) | 95.95 | n/a (in-process) | 604.49 | 19.50 | 210.10 | 55.10 |
| K-V1_core_phase7-rep1 | MEASURED | inprocess | 1 | 62.73 | 58.90 | n/a (in-process) | 98.57 | n/a (in-process) | 629.45 | 25.20 | 212.00 | 60.30 |
| K-V2_core_drift-rep1 | MEASURED | inprocess | 1 | 52.43 | 66.80 | n/a (in-process) | 100.08 | n/a (in-process) | 637.24 | 19.90 | 212.80 | 64.70 |
| K-V3_full-rep1 | MEASURED | inprocess | 1 | 33.80 | 63.50 | n/a (in-process) | 101.46 | n/a (in-process) | 647.82 | 23.60 | 213.10 | 67.80 |
| K-V1_core_phase7-rep2 | MEASURED | inprocess | 1 | 46.54 | 59.90 | n/a (in-process) | 102.33 | n/a (in-process) | 696.52 | 21.50 | 213.20 | 90.50 |
| K-V2_core_drift-rep2 | MEASURED | inprocess | 1 | 82.53 | 67.60 | n/a (in-process) | 102.33 | n/a (in-process) | 665.42 | 22.80 | 213.30 | 72.40 |
| K-V3_full-rep2 | MEASURED | inprocess | 1 | 48.02 | 63.50 | n/a (in-process) | 102.33 | n/a (in-process) | 682.36 | 18.90 | 213.80 | 65.00 |
| K-V0_core-rep2 | MEASURED | inprocess | 1 | 93.74 | 66.30 | n/a (in-process) | 102.33 | n/a (in-process) | 683.21 | 24.70 | 214.30 | 58.30 |
| K-V2_core_drift-rep3 | MEASURED | inprocess | 1 | 67.15 | 67.00 | n/a (in-process) | 102.33 | n/a (in-process) | 683.02 | 21.90 | 214.60 | 81.00 |
| K-V3_full-rep3 | MEASURED | inprocess | 1 | 47.46 | 63.30 | n/a (in-process) | 102.33 | n/a (in-process) | 700.66 | 19.90 | 217.40 | 63.40 |
| K-V0_core-rep3 | MEASURED | inprocess | 1 | 68.25 | 66.40 | n/a (in-process) | 102.33 | n/a (in-process) | 701.08 | 21.40 | 215.20 | 63.50 |
| K-V1_core_phase7-rep3 | MEASURED | inprocess | 1 | 34.26 | 61.10 | n/a (in-process) | 102.33 | n/a (in-process) | 727.53 | 22.00 | 216.70 | 69.80 |
| L-http-c1-b1 | MEASURED | http | 1 | 33.96 | 4.30 | 57.80 | 105.71 | 117.94 | 860.11 | 23.90 | 217.90 | 75.50 |
| L-http-c2-b1 | MEASURED | http | 2 | 32.89 | 5.20 | 96.40 | 110.69 | 118.82 | 900.54 | 36.10 | 220.70 | 155.50 |
| L-http-c4-b1 | MEASURED | http | 4 | 38.85 | 6.00 | 120.00 | 112.07 | 120.54 | 923.31 | 35.20 | 227.50 | 163.00 |
| L-http-c8-b1 | MEASURED | http | 8 | 28.57 | 4.40 | 136.20 | 113.82 | 123.72 | 970.59 | 32.00 | 240.20 | 157.10 |
| L-http-c1-b50 | MEASURED | http | 1 | 37.63 | 3.10 | 60.50 | 122.69 | 127.12 | 1,005.09 | 22.40 | 221.30 | 70.80 |
| L-http-c2-b50 | MEASURED | http | 2 | 58.59 | 4.10 | 93.20 | 132.17 | 130.96 | 1,049.99 | 34.30 | 224.90 | 99.10 |
| L-http-c4-b50 | MEASURED | http | 4 | 37.33 | 3.20 | 120.50 | 147.73 | 139.26 | 1,110.56 | 34.90 | 234.10 | 130.50 |
| L-http-c8-b50 | MEASURED | http | 8 | 40.61 | 2.90 | 135.70 | 170.00 | 154.51 | 1,196.13 | 28.30 | 246.90 | 151.80 |
| L-http-c1-b100 | MEASURED | http | 1 | 44.15 | 2.70 | 61.30 | 146.05 | 133.40 | 1,166.02 | 21.50 | 225.20 | 67.20 |
| L-http-c2-b100 | MEASURED | http | 2 | 51.57 | 4.90 | 93.20 | 152.27 | 140.02 | 1,227.58 | 35.60 | 229.00 | 100.30 |
| L-http-c4-b100 | MEASURED | http | 4 | 51.54 | 4.30 | 118.90 | 178.27 | 155.71 | 1,294.92 | 31.90 | 234.10 | 130.90 |
| L-http-c8-b100 | MEASURED | http | 8 | 38.39 | 3.10 | 132.70 | 189.27 | 181.07 | 1,375.83 | 29.20 | 249.30 | 151.30 |
| sustained | MEASURED | http | 4 | 37.40 | 8.20 | 121.60 | 103.38 | 148.64 | 1,448.72 | 39.20 | 241.40 | 140.40 |

## L. Bottleneck analysis

### MEASURED

| Stage (`B-inproc-v3_full-10000`) | Kind | ms/event | Share of mean latency |
|---|---|---|---|
| `phase7_raw_archive` | MEASURED | 4.64 | 17.1% |
| `event_insert_commit_refresh` | MEASURED | 4.58 | 16.9% |
| `hook:compact_lineage` | MEASURED | 4.47 | 16.5% |
| `phase5_drift` | MEASURED | 2.63 | 9.7% |
| `adapter_registry_lookup` | MEASURED | 2.18 | 8.0% |
| `phase7_overflow_write` | MEASURED | 1.10 | 4.1% |
| `pipeline` | MEASURED | 0.91 | 3.4% |
| `phase7_spill_prepare` | MEASURED | 0.61 | 2.3% |

- The deterministic pipeline is 0.9089 ms of a 27.07 ms mean event (3.4%). The rest is persistence and per-event database work, performed serially for each event.
- In the same run the application process used 66.2 % of one core: it was waiting (on PostgreSQL round trips, commits and fsync) the rest of the time.
- Without the database the same events process at 1,623.26 events/s; with it, 36.82 events/s.
- HTTP, batch 1: 1 conn → 33.96 ev/s, server CPU 57.8 %, DB CPU 23.9 %, mean active PG backends 0.176; 2 conn → 32.89 ev/s, server CPU 96.4 %, DB CPU 36.1 %, mean active PG backends 0.27; 4 conn → 38.85 ev/s, server CPU 120.0 %, DB CPU 35.2 %, mean active PG backends 0.276; 8 conn → 28.57 ev/s, server CPU 136.2 %, DB CPU 32.0 %, mean active PG backends 0.27.
- HTTP, batch 50: 1 conn → 37.63 ev/s (server CPU 60.5 %); 2 conn → 58.59 ev/s (server CPU 93.2 %); 4 conn → 37.33 ev/s (server CPU 120.5 %); 8 conn → 40.61 ev/s (server CPU 135.7 %).
- HTTP, batch 100: 1 conn → 44.15 ev/s (server CPU 61.3 %); 2 conn → 51.57 ev/s (server CPU 93.2 %); 4 conn → 51.54 ev/s (server CPU 118.9 %); 8 conn → 38.39 ev/s (server CPU 132.7 %).
- WAL fsync (commit durability) cost, MEASURED in 25 runs: 0.861–1.864 ms per fsync, 0.900–1.953 ms of WAL fsync per event.

### LIKELY (technically plausible, not isolated by these measurements)

- One synchronous transaction + COMMIT per event (by design, for per-event durability) bounds each connection's rate; batching the HTTP request does not batch the commits.
- A single Python process (GIL) runs parsing, normalization and the ORM work for all connections; threads mostly overlap waits.
- Index maintenance and vacuum costs grow with table size; every run here starts from an empty table.
- Docker Desktop's virtualized disk makes each fsync (PostgreSQL WAL and the raw vault) slower than on a dedicated host.

### UNKNOWN

- Behavior with more than one API process or more than one PostgreSQL instance (not run).
- Behavior on server hardware with local NVMe and dedicated cores (not run).

## M. Integrity verification

Accepted = events the harness sent (warm-up + measured) and the API accepted. Every check runs against the benchmark database only; the database is reset per run, so each row covers exactly one run.

**Duplicates.** Some malformed kinds are constant strings (a truncated CEF header, an incomplete LEEF header, a truncated syslog header), so the harness itself sends a few byte-identical raw logs per run. LogForge does not deduplicate (documented behavior), so each is correctly stored as its own event. A run passes only if the duplicate groups stored equal the duplicate groups *sent* (regenerated from the deterministic dataset; the sustained run compares the full SHA-256 multiset of stored rows with the multiset sent). Anything else — a missing, extra or unexpected duplicate row — fails the run.

| Run | Kind | Accepted | Rows in DB | Missing | Extra | Dup raw-hash groups stored / sent | SHA-256 mismatches | Vault re-hash OK / stored | Vault mismatches | Vault missing | Merkle chain | Events sealed | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K-V0_core-rep1 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V1_core_phase7-rep1 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| K-V2_core_drift-rep1 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V3_full-rep1 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| K-V1_core_phase7-rep2 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| K-V2_core_drift-rep2 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V3_full-rep2 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| K-V0_core-rep2 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V2_core_drift-rep3 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V3_full-rep3 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| K-V0_core-rep3 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 0/0 | 0 | 0 | valid | 1,100 | PASS |
| K-V1_core_phase7-rep3 | MEASURED | 1,100 | 1,100 | 0 | 0 | 4 / 4 | 0 | 1100/1100 | 0 | 0 | valid | 1,100 | PASS |
| L-http-c1-b1 | MEASURED | 2,050 | 2,050 | 0 | 0 | 4 / 4 | 0 | 2050/2050 | 0 | 0 | valid | 2,050 | PASS |
| L-http-c2-b1 | MEASURED | 2,050 | 2,050 | 0 | 0 | 4 / 4 | 0 | 2050/2050 | 0 | 0 | valid | 2,050 | PASS |
| L-http-c4-b1 | MEASURED | 2,050 | 2,050 | 0 | 0 | 4 / 4 | 0 | 2050/2050 | 0 | 0 | valid | 2,050 | PASS |
| L-http-c8-b1 | MEASURED | 2,050 | 2,050 | 0 | 0 | 4 / 4 | 0 | 2050/2050 | 0 | 0 | valid | 2,050 | PASS |
| L-http-c1-b50 | MEASURED | 2,050 | 2,050 | 0 | 0 | 4 / 4 | 0 | 2050/2050 | 0 | 0 | valid | 2,050 | PASS |
| L-http-c2-b50 | MEASURED | 2,100 | 2,100 | 0 | 0 | 4 / 4 | 0 | 2100/2100 | 0 | 0 | valid | 2,100 | PASS |
| L-http-c4-b50 | MEASURED | 2,200 | 2,200 | 0 | 0 | 4 / 4 | 0 | 2200/2200 | 0 | 0 | valid | 2,200 | PASS |
| L-http-c8-b50 | MEASURED | 2,400 | 2,400 | 0 | 0 | 4 / 4 | 0 | 2400/2400 | 0 | 0 | valid | 2,400 | PASS |
| L-http-c1-b100 | MEASURED | 2,100 | 2,100 | 0 | 0 | 4 / 4 | 0 | 2100/2100 | 0 | 0 | valid | 2,100 | PASS |
| L-http-c2-b100 | MEASURED | 2,200 | 2,200 | 0 | 0 | 4 / 4 | 0 | 2200/2200 | 0 | 0 | valid | 2,200 | PASS |
| L-http-c4-b100 | MEASURED | 2,400 | 2,400 | 0 | 0 | 4 / 4 | 0 | 2400/2400 | 0 | 0 | valid | 2,400 | PASS |
| L-http-c8-b100 | MEASURED | 2,800 | 2,800 | 0 | 0 | 4 / 4 | 0 | 2800/2800 | 0 | 0 | valid | 2,800 | PASS |
| sustained | MEASURED | 12,738 | 12,738 | 0 | 0 | 6 / 6 | 0 | 12738/12738 | 0 | 0 | valid | 12,738 | PASS |

V0 and V2 run with the cold vault switched off by design: their raw payloads stay in PostgreSQL (`event_raw_storage` status FAILED, backend `disabled`), so they show 0 stored vault objects.

Malformed input producing PARTIAL or FAILED is expected behavior: those events are persisted with raw bytes and SHA-256 and are included in *Rows in DB* and the SHA-256 check.

Sustained run statuses: {"FAILED": 429, "PARTIAL": 72, "SUCCESS": 9117, "UNDER_REVIEW": 3120}.

**Development database untouched (MEASURED, read-only transactions):**

| Check | Kind | Database | Events | Events with benchmark markers | Latest event |
|---|---|---|---|---|---|
| before-sustained | MEASURED | logforge | 42 | 0 | 2026-09-27 10:47:48.184589+00:00 |
| after-all-benchmarks | MEASURED | logforge | 42 | 0 | 2026-09-27 10:47:48.184589+00:00 |

Markers are attribute names only the benchmark generator emits (`cfgattr000=`, `flexString000=`, `customAttr000=`, `ext_attr_000`, `<Attr000>`, ` pad000=`).

## N. 1B-events/day calculation

```
required average events/s for 1B/day = 1,000,000,000 / 86,400 = 11,574.07 events/s
```

| Target | Required avg events/s | Measured basis | Kind | Measured events/s | Kind | Gap factor | Events/day at measured rate |
|---|---|---|---|---|---|---|---|
| 100 million/day | 1,157.41 | sustained steady state (HTTP) | MEASURED | 37.40 | EXTRAPOLATION — NOT MEASURED | 30.9× | 3,231,360 |
| 100 million/day | 1,157.41 | best default-configuration run `L-http-c2-b50` | MEASURED | 58.59 | EXTRAPOLATION — NOT MEASURED | 19.8× | 5,062,176 |
| 500 million/day | 5,787.04 | sustained steady state (HTTP) | MEASURED | 37.40 | EXTRAPOLATION — NOT MEASURED | 154.7× | 3,231,360 |
| 500 million/day | 5,787.04 | best default-configuration run `L-http-c2-b50` | MEASURED | 58.59 | EXTRAPOLATION — NOT MEASURED | 98.8× | 5,062,176 |
| 1 billion/day | 11,574.07 | sustained steady state (HTTP) | MEASURED | 37.40 | EXTRAPOLATION — NOT MEASURED | 309.5× | 3,231,360 |
| 1 billion/day | 11,574.07 | best default-configuration run `L-http-c2-b50` | MEASURED | 58.59 | EXTRAPOLATION — NOT MEASURED | 197.5× | 5,062,176 |

No target was reached or approached. The arithmetic assumes a constant rate for 24 h, which was not run, and says nothing about peaks (real traffic peaks above its average). It is **not** evidence of production capacity, and it is not evidence that N instances would deliver N× the rate: every instance of this design would write to the same PostgreSQL primary with one commit per event.

Hot PostgreSQL growth measured at 11,457 B/event (event-scoped tables incl. indexes, this dataset's size mix) → EXTRAPOLATION — NOT MEASURED: 11.5 TB/day at 1B events/day, before the cold-vault copy.

## O. What is actually demonstrated

- 70 completed benchmark runs (sections D–I), each with its environment, config and per-event samples.
- Throughput, p50/p95/p99, statuses, raw preservation and SHA-256 agreement for every run.
- Per-stage ingestion cost (in-process runs).
- Process CPU and RSS (harness, API server), backend container cgroup memory.
- PostgreSQL: database transaction/row counters, cluster WAL and checkpoint counters, client-backend state/wait sampling (completion plan and sustained), container CPU/memory via `docker stats`.
- Off-path jobs: Phase 8 statistical drift, shadow validation, Merkle sealing, compact-lineage storage.
- HTTP concurrency × batch sweep: 12 of 12 combinations.
- V0–V3 comparison: V0_core × 3, V1_core_phase7 × 3, V2_core_drift × 3, V3_full × 3.
- One sustained run: 300 s steady state + 30 s warm-up, with per-window evidence.
- Post-run integrity: counts, duplicates, SHA-256 in PostgreSQL, vault re-hash, Merkle chain (completion plan and sustained).

## P. What is NOT demonstrated

**UNKNOWN / not measured:**

- Throughput with more than one API process / replica, and where PostgreSQL saturates under it.
- PostgreSQL commit latency as a distribution (only averages from pg_stat_wal are available; runs before the completion plan did not time WAL at all).
- Behavior over hours or days, and at production table sizes (10⁸–10¹⁰ rows), including vacuum and index bloat.
- Results on server-class hardware (dedicated cores, NVMe, no virtualization layer).
- Throughput of any queue/worker/batched-commit architecture (not built).
- Behavior with real (non-synthetic) traffic mixes and error rates.

**EXTRAPOLATED / PROJECTED only (never observed):**

- Any events/day figure (measured events/s × 86,400) and the gap factors to 100M / 500M / 1B events/day.
- Storage per day at the measured bytes/event.
- Any benefit of more API processes or replicas.

**Limitations:**

- **Environment speed variance (MEASURED).** The pure-CPU `pipeline` stage on the same data ranged from 0.661 ms/event (`K-V2_core_drift-rep2`) to 1.488 ms/event (`K-V1_core_phase7-rep3`) — a 2.3× spread caused by the machine, not the code. Single-run differences inside that spread are not meaningful.
- **Laptop-class, shared environment.** Intel Core 5 120U laptop, Docker Desktop (WSL2) VM; the load generator shares CPUs with the API process; PostgreSQL is shared with the running development stack.
- **Duration.** One 5-minute sustained run; fixed-size runs last seconds to a few minutes.
- **Database.** Every run starts from an empty benchmark database; `shared_buffers=128MB`, default tuning; `xact_commit` counts include read-only transactions; WAL/checkpoint counters are cluster-wide.
- **Reproducibility.** Deterministic data and configuration, but absolute numbers depend on this machine's momentary speed (see the variance item above).
- **Historical comparison.** V0–V3 are switches on the current code, not historical releases. Phase 7 spill preparation and the `event_raw_storage` row run in every persisting configuration (they cannot be switched off without a code change); their cost is shown as stages.
- **Concurrency.** Client connections to one uvicorn worker only; no multi-worker or multi-replica runs.
- **HTTP benchmark.** The server runs without `--reload` and access logging (unlike the dev compose service); client and server share a container; HTTP/1.1 keep-alive, no TLS, no proxy.
- **Scale extrapolation.** Linear arithmetic only; peaks, growth, retention and multi-instance behavior are not modeled.
- **Dataset.** Synthetic; size bands give each vendor source several structures, so ~25 % of events are Phase 5 drift holds (`UNDER_REVIEW`), which write the same rows as normal events.
- **Scheduler.** Off in fixed-size runs (sealing measured as a job); on in the sustained run.

## Q. Recommended next experiment — Horizontal Scaling / Load Balancing

**DO NOT IMPLEMENT LOAD BALANCING YET.** The answers below are derived only from the measurements above.

**A. Is the application CPU-bound?** API server CPU (% of one core) by connections, batch 1: 1→57.80, 2→96.40, 4→120.00, 8→136.20. Throughput over the same runs: 1→33.96, 2→32.89, 4→38.85, 8→28.57 events/s. From 2 connections on, the process uses ≥ 85 % of a core while throughput does not rise with it: more CPU is spent without more events processed (**MEASURED**). Values above 100 % show some work runs outside the Python GIL (C extensions, the database driver). That the limit is CPU/GIL contention inside the single process is **LIKELY**, not proven.

**B. Is PostgreSQL-bound behavior visible?** DB container CPU % (docker stats): 1→23.90, 2→36.10, 4→35.20, 8→32.00; mean simultaneously active PG backends: 1→0.18, 2→0.27, 4→0.28, 8→0.27. PostgreSQL CPU stayed below 80 % of one core and on average at most 0.28 backends were active at once: PostgreSQL saturation is **not** shown by these measurements. Across the batch-1 sweep and the sustained run, PostgreSQL client backends were `idle in transaction` (waiting for the application inside an open transaction) 59 % of sampled time, executing on CPU 4 %, and waiting on WAL write/sync 1 % (**MEASURED**): the database mostly waits for the application, not the reverse.

**C. Would multiple FastAPI replicas plausibly improve throughput?** Best speed-up over 1 connection: 1.14× at 4 connections; at 8 connections 0.84×, with p95 latency 495.279 ms vs 58.973 ms at 1 connection (**MEASURED**, single runs). More connections to one API process therefore mostly add queueing, not throughput. Because the single API process shows the CPU/queueing signs above while PostgreSQL is mostly idle, more API processes are a **plausible** way to raise throughput until PostgreSQL (one commit per event, WAL fsync ≈ 1–2 ms) becomes the limit — an inference, **not a measurement**.

**D. Evidence:** sections F (sweep, scaling efficiency), G (sustained), J (PostgreSQL/WAL), K (CPU/RAM), L (stage costs).

**E. Recommended experiment for Step 13 (not implemented here):**

| Parameter | Value |
|---|---|
| Replicas | 1 → 2 → 4 API processes (uvicorn workers, or replicas behind one reverse proxy) |
| Database | the same single PostgreSQL (`logforge_bench`), reset per run, `track_wal_io_timing` on |
| Dataset / seed | this dataset, seed 1337, 5 % malformed, all formats and size bands |
| Workload | HTTP `POST /ingest` batch 1 and `/ingest/batch` batch 100; total connections fixed per step (e.g. 4 and 8) so only the replica count changes; 2,000 events per run + a 5-minute sustained run at the best replica count |
| Repetitions | 3 per configuration, interleaved order, same warm-up |
| Record | events/s, p50/p95/p99, errors/timeouts, per-process CPU and RSS, PostgreSQL CPU/memory, commits/s, rows/s, WAL MB/s, WAL fsync ms, `pg_stat_activity` waits, lock waits |
| Integrity | the same post-run checks as here: counts, SHA-256 multiset, vault re-hash, Merkle chain |
| Stop / decide | stop adding replicas when events/s stops rising or PostgreSQL CPU, WAL-sync or lock waits dominate; report scaling efficiency = (events/s at N ÷ at 1) ÷ N |

**Conclusion:** INSUFFICIENT EVIDENCE — LOAD BALANCING EXPERIMENT SHOULD BE THE NEXT STEP. No multi-process or multi-replica configuration was measured, so any horizontal-scaling gain is unproven.

## R. Step 10 final verdict

Computed by `report._verdict` from the artifacts; not typed by hand.

| Acceptance criterion | Result | Evidence |
|---|---|---|
| Standard suite complete (46 runs, 0 failed) | PASS | 46 of 46 completed (planned 46) |
| Completion suite complete (24 runs, 0 failed) | PASS | 24 of 24 completed (planned 24) |
| HTTP concurrency sweep 1/2/4/8 connections x batch 1/50/100 | PASS | 12 of 12 combinations measured |
| A 10,000-event HTTP run with several connections | PASS | G-http-v3_full-10000 |
| Real 5-minute sustained run completed | PASS | status COMPLETED, steady state 300 s + warm-up 30 s |
| Sustained run: no application degradation threshold crossed (throughput, p95, errors, API-server RSS) | PASS | no flag (throughput ratio 0.903, p95 ratio 1.101, errors 0); NOTE memory thresholds crossed by non-application series: harness_rss_mb, cgroup_mb (section G) |
| PostgreSQL evidence collected (activity, WAL, checkpoints) | PASS | sustained run + completion plan |
| Integrity verified (counts, duplicates, SHA-256, vault, Merkle) | PASS | 25 of 25 verified runs passed |
| Development database untouched by the benchmark | PASS | benchmark-marker events in the dev DB per check: 0, 0 |
| Tests pass (JUnit reports) | PASS | backend-full: 864/864 passed; phase3: 87/87 passed; phase5: 112/112 passed; phase6: 71/71 passed; phase7: 160/160 passed; phase8: 247/247 passed; bench-harness: 14/14 passed; frontend: 64/64 passed |

Test reports (MEASURED, read from JUnit XML):

| Suite | Tests | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| backend-full | 864 | 864 | 0 | 0 | 0 |
| phase3 | 87 | 87 | 0 | 0 | 0 |
| phase5 | 112 | 112 | 0 | 0 | 0 |
| phase6 | 71 | 71 | 0 | 0 | 0 |
| phase7 | 160 | 160 | 0 | 0 | 0 |
| phase8 | 247 | 247 | 0 | 0 | 0 |
| bench-harness | 14 | 14 | 0 | 0 | 0 |
| frontend | 64 | 64 | 0 | 0 | 0 |

**STEP 10 COMPLETE — ALL ACCEPTANCE CRITERIA PASSED**
