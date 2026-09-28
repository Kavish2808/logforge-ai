# Phase 8 — Compact lineage, statistical/semantic drift, golden baselines (Steps 3–5)

Everything below is additive. It plugs into the system only through the three
Phase 7 hooks (persist hook H3, scheduler step H2, pre-action guard H1) and
the tables of migration `0008_phase8_resilience`. No Phase 0–7 table,
migration, state machine or API changes. `PHASE8_ENABLED=false` registers no
hook, guard or step (Phase 7 behavior); the read APIs stay available.

| Piece | Code |
|---|---|
| Lineage codec (pure) | `backend/app/phase8/lineage_codec.py` |
| Compact lineage service (H3 hook, H2 backfill) | `backend/app/services/phase8/compact_lineage_service.py` |
| Statistical signals (pure) | `backend/app/phase8/drift_stats.py` |
| Semantic advisories (pure) | `backend/app/phase8/drift_semantic.py` |
| Statistical + semantic service | `backend/app/services/phase8/advanced_drift_service.py` |
| Golden baselines + poisoning guard (H1) | `backend/app/services/phase8/golden_baseline_service.py` |
| API | `backend/app/api/routes/phase8.py` (prefix `/api/v1`) |
| Wiring | `backend/app/phase8/register.py` |

---

## Step 3 — Compact, exception-based lineage

The detailed lineage (`GET /api/v1/views/events/{id}/lineage`) is unchanged
and stays the source of truth. The compact form is a **point-in-time
projection** of it, stored as one row per event in `event_lineage_compact`.

### Template v1

| Column | Type | Content |
|---|---|---|
| `stage_mask` | BIGINT | bits 0–17: 9 stages × 2 bits; bits 56–62: template version; everything else must be 0 |
| `exception_mask` | INTEGER | bits 0–7: `FAILED PARTIAL DRIFT OVERFLOW VAULT_FAILED LEARNING ROLLBACK REPLAY`; bits 8–31 must be 0 |
| `template_version` | SMALLINT | `1` (must equal the version embedded in `stage_mask`) |
| `is_exception` | BOOLEAN | any exception bit, or any stage `WARN`/`FAIL` (partial index for exception scans) |

Stages, in bit order: `RAW FORMAT PARSER ADAPTER NORMALIZATION FIELD_ACCOUNTING WARNINGS DRIFT BASELINE`
(detailed names `FORMAT_DETECTION` → `FORMAT`, `DRIFT_DECISION` → `DRIFT`).
Outcome codes: `SKIPPED=0 OK=1 WARN=2 FAIL=3` — exactly the four outcomes the
detailed API reports, ordered by severity. Wire form (`packed_hex`): 5 bytes
(version, 3 stage bytes, exception byte).

Decoding is strict: negative values, reserved bits, unknown template
versions, or a `template_version` column that disagrees with the embedded
version raise `LineageDecodeError`; the API returns `decodable: false` with
the reason instead of guessing.

### How rows are written

- **New events**: the H3 persist hook computes the row in the ingest
  transaction, inside its own SAVEPOINT. A failure never affects the event
  (Phase 7 guarantee) — the event simply has no row yet.
- **Reprocessed events**: H3 fires *before* the reprocessed fields are
  assigned, so the row is recomputed in a `before_commit` listener of that
  transaction (never from stale fields). A rolled-back reprocess leaves
  nothing pending.
- **Backfill**: scheduler step `compact_lineage_backfill` fills events
  without a row (pre-Phase-8 events, hook failures), 500 per tick.
- Stage outcomes reuse the exact helpers of `views_service.lineage`, so the
  projection cannot diverge by construction; tests assert equivalence.
- `LEARNING / ROLLBACK / REPLAY` are owned by their Phase 8 services
  (`compact_lineage_service.mark_exception`) and survive recomputation.
  The other five bits are derived from the event and its Phase 7 evidence rows.

### What is intentionally *not* in the compact row

Per-stage summaries/details, field-level accounting, the `LEARNING_HISTORY`
stage (represented by the `LEARNING` bit) and Phase 7 evidence facts. The
API response lists these under `not_in_compact_form` and links the detailed
lineage. Because the detailed lineage is computed at read time, a later
baseline/adapter change can make an older compact row differ from it;
`?verify=true` reports that explicitly (`stale`, `stage_differences`).

### API

| Method | Path | Notes |
|---|---|---|
| GET | `/lineage/compact/{event_id}?verify=` | stored row (or `persisted:false` computed on read), decoded stages/exceptions, `packed_hex`, optional verification against the detailed lineage |
| GET | `/lineage/compact/stats` | rows, missing rows, exception rows, per-bit and per-stage counts, table size |
| GET | `/lineage/compact/benchmark?sample=` | measured storage (see below) |

### Storage benchmark

`/lineage/compact/benchmark` measures with PostgreSQL's own `pg_column_size`
on the most recent `sample` events: the whole compact row (incl. the 26-char
`event_id` key and row header), the four payload columns, and the detailed
lineage JSON document both as text bytes and as JSONB. It also times one
compact computation vs. one detailed lineage build. Index/TOAST overhead is
excluded on both sides. The detailed lineage is not stored by LogForge; the
comparison shows what storing it would cost.

Measured during verification (Docker Desktop on Windows 11, PostgreSQL 16
container, single process; not a scale claim):

| Measurement | Value |
|---|---|
| Packed form | 5 bytes |
| Compact payload columns (`pg_column_size`) | 15 bytes |
| Whole compact row incl. 26-char key + header | 80 bytes |
| Detailed lineage JSON / JSONB, avg over 47 dev events | ~4.65 KB / ~5.42 KB (≈ 68× the compact row) |
| H3 hook inside ingest (SAVEPOINT + 2 lookups + upsert) | median ~5.8 ms, p95 ~10.6 ms (300 ingests) |
| End-to-end ingest median, hook off → on | ~22.7 ms → ~25.7 ms (median of 4 alternating rounds × 300) |

The hook cost is dominated by database round-trips (≈ 5 per event); for an
inline (non-spilled) new event the overflow lookup is skipped because Phase 7
writes an overflow row only for events marked `SPILLED`.

---

## Step 4 — Statistical + semantic drift

`Structural (Phase 5, unchanged) → Statistical → Semantic (advisory)`

### Scope and bounds

- Source = `adapter_id`. Monitored fields: `event_action`, `severity`,
  `network.protocol`, `network.dst_port`, `network.direction`,
  `user.domain`, plus opt-in `extensions.<key>` (≤ 10 per run; inline
  extensions only — spilled overflow values are not read).
- Windows: current `[end − 1h, end)`, baseline the 168 h before it; `end`
  defaults to the start of the current UTC hour (reruns within the hour are
  identical).
- Minimum 200 events in **each** window, else the source is reported as
  `SKIPPED_INSUFFICIENT_SAMPLES` with its counts.
- ≤ 20 000 events per window (newest first, ties by `event_id`,
  `*_truncated` flags), ≤ 50 sources per run, top-K = 20 categories
  (rest bucketed as `__OTHER__`). FAILED events are excluded.

### Signals

| Metric | Deviation | Threshold (MEDIUM) | HIGH at |
|---|---|---|---|
| `PSI` | Σ (c−b)·ln(c/b), ε = 1e-4 | 0.25 | 0.5 |
| `NEW_CATEGORY_SHARE` | share of current values unseen in the baseline | 0.10 | 0.30 |
| `VANISHED_CATEGORY_SHARE` | share of baseline values absent now | 0.10 | 0.30 |
| `NULL_RATE_CHANGE` | \|null rate change\| | 0.10 | 0.30 |
| `CARDINALITY_RATIO` | max(r, 1/r), distinct counts on equal-size samples (≥ 5 distinct) | 2.0 | 4.0 |
| `MEDIAN_SHIFT_IQR` | \|Δ median\| / baseline IQR (IQR 0 → raw units), numeric fields only | 1.0 | 3.0 |

Every finding carries: `source, field, metric, baseline_value,
current_value, deviation, threshold, severity, deterministic_reason
(<METRIC>_AT_OR_ABOVE_THRESHOLD), explanation, evidence_counts,
analysis_window, quality`. There is no composite score. Finding ids are
`sha256(layer|source|field|metric|window_end)`: a rerun updates the same
finding and keeps its review state.

### Semantic advisories (deterministic heuristics, not understanding)

Run only when the statistical layer flagged `event_action` or `severity`,
and only when the structure is stable (no Phase 5 drift in the current
window and ≥ 95 % of current events have a structural signature seen in the
baseline). Always `advisory: true`, linked to the statistical finding via
`parent_finding_id`. No LLM, no network.

- `ACTION_RELABEL_ADVISORY` — `event_action` changed while severity,
  protocol, port and direction stayed statistically stable. Vanished and new
  action values (each ≥ 2 % share) are paired when their context distribution
  over (severity, protocol, dst_port, direction) is ≥ 0.80 similar
  (1 − total variation distance) and their shares are within 2×.
- `SEVERITY_REMAP_ADVISORY` — `severity` changed while `event_action` stayed
  stable; for actions with ≥ 5 % share in both windows the modal severity is
  compared.

### API

| Method | Path | Governance |
|---|---|---|
| POST | `/drift/statistical/analyze` | `review` capability (anonymous only in permissive RBAC); audited `DRIFT_STATISTICAL_ANALYZE` |
| GET | `/drift/findings?layer=&source_key=&status=&limit=` | read-only |
| GET | `/drift/findings/{id}` | read-only; includes semantic children |
| POST | `/drift/findings/{id}/acknowledge` | `review`; audited `DRIFT_FINDING_ACKNOWLEDGE` |

Phase 5 drift (`/drift/baselines`, `/events/{id}/drift/accept`) is untouched;
the analysis never writes events or baselines.

---

## Step 5 — Golden + current dual baseline

- **CURRENT baseline** = the existing Phase 5 `source_baselines` row. It
  keeps evolving through drift review and Phase 6 learning, unchanged.
- **GOLDEN baseline** = an explicitly pinned, versioned snapshot: reference
  + accepted variants, Phase 5 baseline version, adapter id/version, and a
  bounded statistical profile (top-K distributions over 168 h; marked
  `sufficient:false` below 200 events). Only the golden endpoints write it,
  only for an authenticated `SOC_ADMIN` with a non-empty note, in every RBAC
  mode. Superseded/retired versions are kept.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/golden-baselines` | active goldens + policy |
| GET | `/golden-baselines/{source}` | active + all versions |
| GET | `/golden-baselines/{source}/compare?hours=` | explainable CURRENT vs GOLDEN (structural similarity, accepted changes since golden, PSI per field when both profiles have ≥ 200 events); read-only |
| GET | `/golden-baselines/comparisons?source_key=` | stored guard evidence |
| POST | `/golden-baselines/{source}` | pin (409 if one is active; 409 if the source has no Phase 5 baseline — nothing is invented) |
| PUT | `/golden-baselines/{source}` | re-pin → new version, previous `SUPERSEDED`; optional `expected_version`; maker-checker: an actor who approved baseline changes since the current golden cannot bless them |
| POST | `/golden-baselines/{source}/retire` | `RETIRED` (guard goes silent for that source) |

All attempts are audited (`GOLDEN_BASELINE_PIN / _REPIN / _RETIRE`, SUCCESS /
DENIED / FAILED) in the Phase 7 hash chain.

### Poisoning guard

Registered through the Phase 7 guard hook on `DRIFT_ADD_VARIANT`,
`DRIFT_REPLACE_BASELINE`, `LEARNING_ACTIVATE` and `LEARNING_APPROVE` with
`activate: true` (the one-call approve-and-activate route would otherwise
bypass the activation guard). With an active golden:

- `ELEVATED` if the proposed structure's Phase 5 comparator similarity to the
  golden (reference + variants) is **< 0.70**, or more than **5** accepted
  changes (`VARIANT_ADDED`, `BASELINE_REPLACED`, `BASELINE_LEARNED` history
  rows after the golden's baseline version) exist;
- otherwise `ALLOW`.

`ELEVATED` is enforced by the unchanged Phase 7 policy: authenticated
`SOC_ADMIN` + non-empty `note`, after RBAC and maker-checker (so a SOC_ADMIN
who proposed a learning session still cannot activate it). Every evaluation
stores a `baseline_comparisons` row (NEW vs CURRENT, NEW vs GOLDEN, CURRENT
vs GOLDEN, steps, reasons, decision) whose id is in the audit record's guard
evidence. With **no** golden the guard returns nothing: no block, no
comparison row, and the audit record is identical to Phase 7.

---

## Known limitations

- Compact lineage is a point-in-time projection; the detailed lineage is
  recomputed on read. Differences are reported (`verify=true`), not hidden.
- The compact lineage hook adds a few milliseconds per ingested event (see the
  measurements above); it is synchronous by design (row available at commit).
- Statistical drift runs on demand (API). It is not scheduled yet.
- Every guard evaluation against a golden stores a `baseline_comparisons`
  row, including attempts that are then denied; the table grows with repeated
  attempts and has no retention policy yet.
- Extension monitoring reads inline extensions only (not spilled overflow).
- Semantic advisories are heuristics over normalized fields; they can miss
  relabels whose context also changed, and they never gate anything.
- Maker-checker for golden re-pinning identifies approvers from the audit
  log; anonymous (permissive-mode) approvals cannot be attributed.
