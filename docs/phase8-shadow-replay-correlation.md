# Phase 8 — Shadow validation, revisions & replay, cross-vendor correlation (Steps 6–8)

Additive, like Steps 1–5: everything plugs in through the Phase 7 guard hook
(H1), scheduler step (H2) and persist hook (H3), and uses tables that already
exist in migration `0008_phase8_resilience` (`shadow_runs`, `replay_jobs`,
`event_revisions`, `drift_correlations`). **No migration was added or
changed.** Phase 3/5/6 state machines, the reprocess engine, hashing and the
Merkle chain are unchanged.

| Piece | Code | Hook |
|---|---|---|
| Shadow validation + circuit breaker | `app/services/phase8/shadow_service.py` | guard `shadow_gate` on `LEARNING_ACTIVATE`, `LEARNING_APPROVE` (activate=true) |
| Event revisions | `app/services/phase8/revision_service.py` | persist hook `event_revisions` |
| Replay jobs + revision-aware rollback | `app/services/phase8/replay_service.py` | scheduler step `replay_worker` |
| Cross-vendor correlation | `app/services/phase8/correlation_service.py` | — (API) |
| API | `app/api/routes/phase8.py` (prefix `/api/v1`) | |

Routes follow the project's existing convention (`/api/v1/<domain>/…`, no
`/phase8` segment), like Steps 3–5.

---

## Step 6 — Stratified shadow validation + circuit breaker

`POST /shadow/runs {learning_session_id}` compares the session's candidate
adapter (NEW) with the runtime registry as it is (OLD) on real stored events.

**Throwaway context.** Both registries are built in memory (the production
adapter is never replaced). Each stored event is re-run through the real
pipeline (`orchestrator.process` + the ingestion field builder) for both
sides; Phase 5 drift classification runs inside a SAVEPOINT that is always
rolled back. Only the `shadow_runs` row is written.

**Strata** (disjoint, in this precedence; newest first, ties by `event_id`;
target 25 each; fewer = all available, marked insufficient, never padded):

| Stratum | Rule |
|---|---|
| FAILED | status FAILED (any source — the candidate must not claim or break malformed input) |
| DRIFT | Phase 5 DRIFT / POSSIBLE_FORMAT_DRIFT recorded for this source |
| PARTIAL_WARNING | this source, PARTIAL or with warnings |
| EXTENSION_HEAVY | this source, more extension keys than the source's own median (or spilled) |
| NORMAL | this source, everything else |
| DIVERSITY | other sources, round-robin by adapter (hijack check) |

The selected event ids per stratum are stored in the run.

**Compared per event:** raw bytes/SHA-256, pipeline status, adapter, every
normalized target value, extensions, warnings, field accounting (the same
`_field_accounting` the lineage API uses), Phase 5 drift classification and
latency (3 interleaved repetitions, median per event; p50/p95 over events).

**Circuit breaker — policy `BLOCK_CRITICAL` → `BLOCKED`:**

| Code | Condition |
|---|---|
| `STATUS_DEGRADED` | SUCCESS→PARTIAL/FAILED or PARTIAL→FAILED |
| `EVIDENCE_LOSS` | a parsed field OLD accounted for (mapped or preserved) is not accounted for by NEW |
| `RAW_HASH_MISMATCH` | NEW output's raw bytes / SHA-256 differ from the stored event |
| `LATENCY_P95` | NEW p95 > 3 × OLD p95 **and** at least 0.5 ms slower (noise floor) |
| `SHADOW_ERROR` | the run itself failed — fails closed |

Otherwise `REVIEW_REQUIRED` if any stratum is under-covered
(`INSUFFICIENT_COVERAGE`) or a non-critical difference exists
(`VALUE_CHANGED`, `MAPPING_DEMOTED`, `ADAPTER_CHANGED`, `WARNINGS_ADDED`,
`EXTENSION_VALUE_CHANGED`, `DRIFT_CLASSIFICATION_CHANGED`); `PASSED` only with
full coverage and no such difference. Improvements (`NEWLY_MAPPED`,
`EXTENSION_PROMOTED`, `STATUS_IMPROVED`, `WARNINGS_REMOVED`) are recorded,
never blocking. State: `RUNNING` (committed before work starts) →
`PASSED` / `REVIEW_REQUIRED` / `BLOCKED`.

**Activation gate** (`PHASE8_SHADOW_GATE`, Phase 7 guard, no Phase 6 code
touched). The run that counts is the newest finished run for the session's
*current* proposal version and candidate digest.

| Mode | No run | PASSED | REVIEW_REQUIRED | BLOCKED |
|---|---|---|---|---|
| `off` | allowed | allowed | allowed | allowed |
| `if_present` (default) | allowed (Phase 6 behavior) | allowed | ELEVATED (SOC_ADMIN + note) | 409 |
| `required` | 409 | allowed | ELEVATED | 409 |

Every run is audited (`SHADOW_RUN`, verdict + reasons); the gate's verdict
appears in the `LEARNING_ACTIVATE` / `LEARNING_APPROVE` audit record.

---

## Step 7 — Revisions, rate-limited replay, revision-aware rollback

### Revisions (`GET /revisions/{event_id}`)

The event row keeps holding the current result (Phase 0 reprocess still
overwrites it in place). The `event_revisions` hook keeps every earlier one:
before the first reprocess of an event its stored state becomes revision 1
(`ORIGINAL`); each reprocess appends a revision right before its transaction
commits — `REPLAY` (with job id, actor, reason) when a replay job drives it,
`REPROCESS` for the manual endpoint. A revision stores a canonical snapshot of
every processing column (no raw bytes — referenced by `raw_hash`) and its
SHA-256; the API re-verifies snapshots, the parent chain, a single current
revision and the raw hash on read. Revisions are never updated (except the
`is_current` pointer) and at most one exists per (event, replay job).

### Replay jobs

| Method | Path |
|---|---|
| POST | `/replay/jobs` `{adapter_id, reason, from_version?, window_start?, window_end?, rate_per_sec \| rate_per_minute, batch_size}` |
| GET | `/replay/jobs`, `/replay/jobs/{id}` |
| POST | `/replay/jobs/{id}/start` · `/pause` · `/resume` · `/cancel` |

States: `PENDING` (or `PENDING_APPROVAL` for > 10,000 events) → `RUNNING` ⇄
`PAUSED` → `COMPLETED` / `FAILED`; `CANCELLED` from any non-terminal state.

- **Selection** is fixed at creation (adapter, optional version, window,
  never events received after creation) and walked with a keyset cursor
  `(received_at, event_id)`; the checkpoint, counters and status are
  committed after every event.
- **Engine**: each event goes through the existing
  `ingestion_service.reprocess_event` (same pipeline, drift evaluation and
  Phase 7 evidence hook). There is no second parsing engine.
- **Rate limit**: token bucket per slice — `min(batch_size, rate × seconds
  since the previous slice)` events (first slice `min(batch_size, ⌈rate⌉)`).
  The scheduler step runs one slice per RUNNING job (≤ 5 jobs) per tick, so
  effective throughput is `min(rate, batch_size / scheduler interval)`.
  Pause/cancel are re-read before every event. A per-job advisory lock
  prevents two workers from running the same job.
- **Idempotency**: an event that already has a revision for the job is
  skipped (unique index), so a lost checkpoint never duplicates revisions.
- **Integrity**: SHA-256 of the raw bytes before == after == stored
  `raw_hash` for every event (a mismatch fails the job at once); the job
  finishes with a Merkle verification of the newest batches; replayed events
  get the `REPLAY` (and for rollback jobs `ROLLBACK`) compact-lineage bit.
- **Safety**: if the target adapter version is no longer active when a slice
  starts, the job fails rather than replay against an unexpected revision.
  One failing event is recorded (`errors`) and the job continues.
- **Governance**: `review` capability like the existing reprocess endpoint;
  every transition audited. **More than 10,000 events**: the creator must be
  an authenticated SECURITY_ENGINEER/SOC_ADMIN with a reason; the job waits
  in `PENDING_APPROVAL` and must be started by a *different* authenticated
  SECURITY_ENGINEER/SOC_ADMIN (maker-checker).

### Revision-aware rollback — `POST /replay/rollback/{adapter_id}`

`{reason, replay=true, rate_per_sec, batch_size}` — `rollback` capability
(SECURITY_ENGINEER), audited `REVISION_ROLLBACK`.

1. Validates a previous approved (SUPERSEDED) version exists; snapshots the
   active/target versions and mapping digests, events per version and Merkle
   state.
2. Rolls back through the existing path: Phase 6 learning rollback when the
   active version came from an active learning session, otherwise Phase 3
   adapter rollback (their state machines are unchanged).
3. Verifies the target is now active and re-checks Merkle.
4. Queues a `ROLLBACK` replay job for the events the withdrawn version
   processed (`PENDING`, started explicitly). Events are never modified by
   the rollback itself; history is only appended.

---

## Step 8 — Cross-vendor drift correlation

`POST /drift/correlations/analyze {window_end?, window_minutes=60}` (audited,
`review`), `GET /drift/correlations`, `GET /drift/correlations/{id}`.

Inputs per source in the window: Phase 5 DRIFT / POSSIBLE_FORMAT_DRIFT
events (changed raw fields resolved to the normalized target they map to;
unmapped → `extensions`) and Phase 8 statistical/semantic findings whose
current window overlaps (field as monitored; `extensions.*` → `extensions`).
Sources are linked by a shared target field or change type; a linked group is
a correlation only with **≥ 2 distinct vendors**.

| Component | Weight | Value |
|---|---|---|
| `source_diversity` | 0.30 | min(1, (vendors − 1) / 3) |
| `change_overlap` | 0.25 | mean Jaccard of change types over cross-vendor source pairs |
| `shared_fields` | 0.30 | mean Jaccard of target fields over cross-vendor source pairs |
| `time_proximity` | 0.15 | 1 − first-seen spread / window |

Score = Σ contributions; strength `HIGH` ≥ 0.65, `MEDIUM` ≥ 0.40, else `LOW`
(descriptive only). Each correlation stores sources, vendors, finding and
event ids, fields, change types, the full breakdown, per-source evidence and
a plain-language explanation. The id is a digest of window + members, so
re-analysis updates the same row. It never changes adapters, drift reviews,
learning, golden baselines, rollbacks or events.

---

## Known limitations

- `PHASE8_SHADOW_GATE` defaults to `if_present` so Phase 6 behavior (and its
  tests) is unchanged; `required` makes shadow validation mandatory.
- Latency comparison is wall-clock inside the API process; a 0.5 ms absolute
  floor avoids blocking on timer noise for sub-millisecond pipelines.
- Revision 1 of a manually reprocessed event cannot hold spilled overflow
  *values* (Phase 7 replaces the overflow row before hooks run); its digest is
  kept. Replay captures revision 1 itself, including overflow values.
- Replay throughput through the scheduler is bounded by
  `batch_size / SCHEDULER_INTERVAL_SECONDS`; measured per-event replay cost
  (reprocess + drift + Phase 7/8 hooks + commits) was ~50–70 ms on Docker
  Desktop, which is the practical limit below the configured rate.
- **Replays of ≤ 10,000 events follow the existing reprocess rule (`review`
  capability). In `RBAC_MODE=permissive` they are therefore accepted
  anonymously (still audited as `anonymous`) and there is no maker-checker
  below the threshold. Requiring authentication and stricter multi-user
  enforcement for all replays is a Phase 9 production-hardening item; it was
  deliberately not redesigned in Phase 8.**
- Correlation groups by linkage within one window; it does not track
  correlations across adjacent windows.
