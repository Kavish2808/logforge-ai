// Phase 8 API: types mirror the backend responses exactly (Steps 2-8); one
// function per endpoint. Nothing here derives or fabricates data.
import { Query, request } from "./client";

type Sig = AbortSignal | undefined;
const enc = encodeURIComponent;

// --- Step 3: compact lineage ---------------------------------------------------------------
export interface CompactLineage {
  event_id: string;
  persisted: boolean;
  template_version: number;
  stage_mask: number;
  exception_mask: number;
  is_exception_column: boolean | null;
  computed_at: string;
  decodable: boolean;
  error?: string;
  stages?: { stage: string; outcome: string }[];
  exceptions?: string[];
  is_exception?: boolean;
  worst_outcome?: string;
  packed_hex?: string;
  note?: string;
  detailed_lineage: string;
  not_in_compact_form: string[];
  verification?: {
    equivalent: boolean;
    stale: boolean;
    stage_differences: { stage: string; compact: string; detailed_now: string }[];
    derived_exceptions_now: string[];
  };
}

export interface CompactLineageStats {
  template_version: number;
  events_total: number;
  rows: number;
  missing_rows: number;
  exception_rows: number;
  normal_rows: number;
  by_exception: Record<string, number>;
  by_stage_outcome: Record<string, Record<string, number>>;
  by_template_version: Record<string, number>;
  table_total_bytes: number;
}

// --- Step 4: statistical / semantic drift ---------------------------------------------------
export interface AnalysisWindow { baseline_start: string; baseline_end: string; current_start: string; current_end: string }

export interface DriftFinding {
  id: string;
  layer: "STATISTICAL" | "SEMANTIC";
  source: string;
  field: string;
  metric: string;
  baseline_value: Record<string, unknown>;
  current_value: Record<string, unknown>;
  deviation: number;
  threshold: number;
  severity: string;
  deterministic_reason: string;
  explanation: string;
  advisory: boolean;
  quality: string;
  evidence_counts: { baseline_events: number; current_events: number };
  analysis_window: AnalysisWindow;
  parent_finding_id: string | null;
  status: string;
  acknowledged_by: string | null;
  created_at?: string;
  evidence?: Record<string, unknown>;
  children?: DriftFinding[];
}

export interface FindingList { items: DriftFinding[]; stats: { by_layer_status: Record<string, number>; total: number } }

export interface AnalysisReport {
  window: Record<string, string>;
  config: Record<string, unknown>;
  sources_analyzed: number;
  sources_truncated: boolean;
  findings: number;
  advisories: number;
  sources: { source_key: string; status: string; reason?: string; baseline_n: number; current_n: number;
    findings?: DriftFinding[]; advisories?: DriftFinding[] }[];
  layers: string[];
}

// --- Step 5: golden / current baselines -----------------------------------------------------
export interface CurrentBaseline {
  source_key: string;
  adapter_id: string;
  adapter_version: string | null;
  format_detected: string;
  origin: string;
  version: number;
  fingerprint: { field_order?: string[]; field_count?: number; signature?: string };
  accepted_variants: { fingerprint: { field_order?: string[] }; accepted_from_event_id?: string | null;
    accepted_at?: string | null; accepted_in_version?: number | null; note?: string | null }[];
  created_at: string;
  updated_at: string;
  under_review_count: number;
  history?: { version: number; action: string; event_id: string | null; field_count: number | null; note: string | null; created_at: string }[] | null;
}

export interface GoldenBaseline {
  id: string;
  source_key: string;
  version: number;
  status: string;
  fingerprint: { reference?: { field_order?: string[] }; variants?: unknown[]; format_detected?: string; baseline_version?: number; baseline_origin?: string };
  statistical_profile: { window?: { start: string; end: string }; n?: number; sufficient?: boolean; min_sample?: number };
  derived_from_baseline_version: number | null;
  approved_by: string;
  approved_role: string | null;
  note: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface GoldenPolicy {
  golden_similarity_threshold: number;
  max_changes_since_golden: number;
  guarded_actions: string[];
  elevated_requires: string;
}

export interface SimilarityDetail {
  similarity: number;
  matched: string;
  components: Record<string, number>;
  added_fields?: string[];
  removed_fields?: string[];
  type_changes?: Record<string, unknown>;
}

export interface GoldenComparison {
  source_key: string;
  golden: { id: string; version: number };
  current_baseline_version: number | null;
  structural: SimilarityDetail | null;
  steps_since_golden: number;
  changes_since_golden: { version: number; action: string; event_id: string | null; note: string | null; created_at: string }[];
  statistical: { compared: boolean; reason?: string; golden_events?: number; current_events?: number;
    fields?: Record<string, { psi: number | null; psi_threshold: number; psi_exceeded: boolean; golden_null_rate: number | null; current_null_rate: number | null }> };
  next_change_would_be_elevated: boolean;
  reasons: string[];
  explanation: string[];
}

export interface GuardComparison {
  id: string;
  source_key: string;
  action: string;
  object_type: string;
  object_id: string | null;
  decision: string;
  poisoning_risk: boolean;
  risk_reasons: { code: string; value: number; threshold: number; detail: string }[];
  steps_since_golden: number | null;
  new_vs_current: Partial<SimilarityDetail>;
  new_vs_golden: SimilarityDetail | null;
  current_vs_golden: SimilarityDetail | null;
  created_at: string;
}

// --- Step 6: shadow validation --------------------------------------------------------------
export interface ShadowReason {
  code: string;
  critical: boolean;
  detail?: string;
  count?: number;
  event_ids?: string[];
  strata?: Record<string, { selected: number; target: number }>;
  kinds?: string[];
  old_p95_ms?: number;
  new_p95_ms?: number;
}

export interface StratumResult {
  target: number;
  available: number;
  selected: string[];
  sufficient: boolean;
  rule?: string;
  results?: { events: number; old_parse_success: number; new_parse_success: number; critical: number; review: number;
    improvements: number; unchanged: number };
}

export interface ShadowTotals {
  status_changes: number; evidence_loss: number; raw_hash_mismatches: number; drift_differences: number;
  old_parse_success: number; new_parse_success: number; review_differences: number; improvements: number;
}

export interface ShadowDiff {
  event_id: string;
  stratum: string;
  raw_hash: string;
  old: { status: string | null; adapter: string | null; version: string | null; drift: string | null };
  new: { status: string | null; adapter: string | null; version: string | null; drift: string | null };
  changes: ({ kind: string } & Record<string, unknown>)[];
}

export interface ShadowRun {
  id: string;
  learning_session_id: string;
  source: string;
  proposal_version: number;
  current_version: string | null;
  candidate_version: string | null;
  status: string;
  verdict: string;
  breaker_tripped: boolean;
  reasons: ShadowReason[];
  sample_count: number;
  strata: Record<string, StratumResult>;
  summary: { status?: string; started_at?: string; finished_at?: string; duration_ms?: number; candidate_sha256?: string;
    totals?: ShadowTotals | null; stratum_counts?: Record<string, number>; circuit_breaker_reason?: string[] | null };
  latency: { old?: { p50: number | null; p95: number | null }; new?: { p50: number | null; p95: number | null };
    repeats_per_event?: number; unit?: string };
  thresholds: { policy?: string; stratum_target?: number; latency_ratio?: number; latency_floor_ms?: number; critical?: string[] };
  created_by: string;
  created_at: string;
  diff?: ShadowDiff[];
}

// --- Step 7: revisions / replay / rollback --------------------------------------------------
export interface Revision {
  id: string;
  event_id: string;
  revision_no: number;
  parent_revision_id: string | null;
  is_current: boolean;
  trigger: string;
  reason: string | null;
  actor: string;
  adapter_id: string | null;
  adapter_version: string | null;
  replay_job_id: string | null;
  resulting_status: string | null;
  processed_at: string | null;
  raw_hash: string;
  snapshot_sha256: string;
  snapshot_verified: boolean;
  raw_hash_matches_event: boolean | null;
  created_at: string;
  snapshot: Record<string, unknown>;
}

export interface RevisionHistory {
  event_id: string;
  event_present: boolean;
  raw_hash: string | null;
  revisions: Revision[];
  count: number;
  current_revision: number | null;
  integrity: { all_snapshots_verified: boolean; raw_hash_unchanged: boolean; parent_chain_intact: boolean; single_current: boolean };
  note: string | null;
}

export interface ReplayJob {
  id: string;
  trigger: string;
  source: string;
  adapter_id: string;
  selection: { from_version: string; window_start: string | null; window_end: string | null; selection_cutoff: string | null };
  target_version: string | null;
  status: string;
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  rate_per_sec: number;
  batch_size: number;
  reason: string;
  created_by: string;
  approved_by: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  checkpoint: { received_at: string; event_id: string } | null;
  large_replay: boolean | null;
  integrity: { raw_hash_mismatches: number; revisions_written: number; merkle: { valid: boolean; scope: string; problems: unknown[] } | null };
  errors: { event_id: string; error: string }[];
  slices: number;
}

export interface RollbackResult {
  adapter_id: string;
  rolled_back_version: number;
  restored_version: number;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  replay_job: ReplayJob | null;
  note: string;
}

// --- Step 8: correlation --------------------------------------------------------------------
export interface Correlation {
  id: string;
  window: { start: string; end: string };
  sources: string[];
  vendors: string[];
  drift_types: string[];
  affected_fields: string[];
  change_types: string[] | null;
  drift_finding_ids: string[];
  event_ids: string[];
  score: number;
  strength: string;
  score_breakdown: Record<string, { value: number; weight: number; contribution: number }>;
  per_source: Record<string, { vendor: string; fields: string[]; change_types: string[]; kinds: string[]; first_seen: string; evidence: number }>;
  explanation: string;
  status: string;
  created_at: string | null;
  investigation_only: boolean;
}

export interface CorrelationReport {
  window: { start: string; end: string; minutes: number };
  sources_with_drift: number;
  correlations: Correlation[];
  rules: Record<string, unknown>;
  safety: string;
}

// --- endpoints ------------------------------------------------------------------------------
export const getCompactLineage = (id: string, verify: boolean, signal?: Sig) =>
  request<CompactLineage>(`/lineage/compact/${enc(id)}`, { query: { verify }, signal });
export const getCompactLineageStats = (signal?: Sig) => request<CompactLineageStats>("/lineage/compact/stats", { signal });

export const getFindings = (query: Query, signal?: Sig) => request<FindingList>("/drift/findings", { query, signal });
export const getFinding = (id: string, signal?: Sig) => request<DriftFinding>(`/drift/findings/${enc(id)}`, { signal });
export const acknowledgeFinding = (id: string, note: string | null) =>
  request<DriftFinding>(`/drift/findings/${enc(id)}/acknowledge`, { method: "POST", body: { note } });
export const runStatisticalAnalysis = (body: { source_key?: string | null; window_end?: string | null }) =>
  request<AnalysisReport>("/drift/statistical/analyze", { method: "POST", body });

export const getCurrentBaselines = (signal?: Sig) => request<{ total: number; items: CurrentBaseline[] }>("/drift/baselines", { signal });
export const getCurrentBaseline = (key: string, signal?: Sig) => request<CurrentBaseline>(`/drift/baselines/${enc(key)}`, { signal });
export const getGoldenBaselines = (signal?: Sig) =>
  request<{ items: GoldenBaseline[]; total: number; policy: GoldenPolicy }>("/golden-baselines", { signal });
export const getGoldenDetail = (key: string, signal?: Sig) =>
  request<{ source_key: string; active: GoldenBaseline | null; versions: GoldenBaseline[] }>(`/golden-baselines/${enc(key)}`, { signal });
export const compareWithGolden = (key: string, signal?: Sig) =>
  request<GoldenComparison>(`/golden-baselines/${enc(key)}/compare`, { signal });
export const getGuardComparisons = (query: Query, signal?: Sig) =>
  request<{ items: GuardComparison[] }>("/golden-baselines/comparisons", { query, signal });
export const pinGolden = (key: string, note: string) =>
  request<GoldenBaseline>(`/golden-baselines/${enc(key)}`, { method: "POST", body: { note } });
export const repinGolden = (key: string, note: string, expectedVersion: number) =>
  request<GoldenBaseline>(`/golden-baselines/${enc(key)}`, { method: "PUT", body: { note, expected_version: expectedVersion } });
export const retireGolden = (key: string, note: string, expectedVersion: number) =>
  request<GoldenBaseline>(`/golden-baselines/${enc(key)}/retire`, { method: "POST", body: { note, expected_version: expectedVersion } });

export const listShadowRuns = (sessionId: string, signal?: Sig) =>
  request<{ items: ShadowRun[] }>("/shadow/runs", { query: { learning_session_id: sessionId }, signal });
export const getShadowRun = (id: string, signal?: Sig) => request<ShadowRun>(`/shadow/runs/${enc(id)}`, { signal });
export const createShadowRun = (sessionId: string) =>
  request<ShadowRun>("/shadow/runs", { method: "POST", body: { learning_session_id: sessionId } });

export const getRevisions = (eventId: string, signal?: Sig) => request<RevisionHistory>(`/revisions/${enc(eventId)}`, { signal });
export const listReplayJobs = (signal?: Sig) => request<{ items: ReplayJob[] }>("/replay/jobs", { query: { limit: 100 }, signal });
export const getReplayJob = (id: string, signal?: Sig) => request<ReplayJob>(`/replay/jobs/${enc(id)}`, { signal });
export const createReplayJob = (body: { adapter_id: string; reason: string; from_version?: string | null; rate_per_sec?: number; batch_size?: number;
  window_start?: string | null; window_end?: string | null }) => request<ReplayJob>("/replay/jobs", { method: "POST", body });
export const replayAction = (id: string, action: "start" | "pause" | "resume" | "cancel") =>
  request<ReplayJob>(`/replay/jobs/${enc(id)}/${action}`, { method: "POST" });
export const revisionRollback = (adapterId: string, body: { reason: string; replay: boolean; rate_per_sec?: number; batch_size?: number }) =>
  request<RollbackResult>(`/replay/rollback/${enc(adapterId)}`, { method: "POST", body });

export const listCorrelations = (query: Query, signal?: Sig) => request<{ items: Correlation[] }>("/drift/correlations", { query, signal });
export const getCorrelation = (id: string, signal?: Sig) => request<Correlation>(`/drift/correlations/${enc(id)}`, { signal });
export const analyzeCorrelations = (body: { window_end?: string | null; window_minutes?: number }) =>
  request<CorrelationReport>("/drift/correlations/analyze", { method: "POST", body });
