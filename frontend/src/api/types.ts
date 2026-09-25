// Response types mirroring the backend schemas (app/schema/*.py).
export type Dict = Record<string, unknown>;

export interface EventRow {
  event_id: string;
  received_at: string;
  event_timestamp: string | null;
  status: string;
  format_detected: string;
  vendor: string | null;
  product: string | null;
  source_key: string | null;
  adapter_id: string | null;
  adapter_version: string | null;
  adapter_source: string | null;
  ocsf_class_name: string | null;
  ocsf_category_name: string | null;
  event_type: string | null;
  event_action: string | null;
  severity: string | null;
  drift_status: string | null;
  drift_severity: string | null;
  warning_count: number;
  preserved_field_count: number;
  raw_hash: string;
}

export interface EventPage {
  items: EventRow[];
  limit: number;
  next_cursor: string | null;
  has_more: boolean;
  total: number | null;
}

export interface TrendBucket {
  bucket: string;
  by_status: Record<string, number>;
  total: number;
}

export interface Summary {
  window: Dict;
  totals: Record<string, number>;
  by_status: Record<string, number>;
  by_format: Record<string, number>;
  by_vendor: Record<string, number>;
  by_adapter: Record<string, number>;
  by_drift_status: Record<string, number>;
  by_drift_severity: Record<string, number>;
  adapters: Record<string, number>;
  onboarding_sessions: Record<string, number>;
  learning_sessions: Record<string, number>;
  trend: TrendBucket[];
}

export interface FilterValues {
  statuses: string[];
  formats: string[];
  vendors: string[];
  products: string[];
  sources: string[];
  adapters: { adapter_id: string; adapter_version: string | null; events: number }[];
  categories: string[];
  severities: string[];
  drift_statuses: string[];
  drift_severities: string[];
}

export interface LineageStage {
  stage: string;
  outcome: "OK" | "WARN" | "FAIL" | "SKIPPED" | string;
  summary: string;
  details: Dict;
}

export interface AccountedField {
  field: string;
  outcome: "MAPPED" | "PRESERVED" | "UNACCOUNTED" | string;
  target?: string;
  location?: string;
  value?: unknown;
  normalized_value_present?: boolean;
}

export interface Lineage {
  event_id: string;
  status: string;
  chain: LineageStage[];
  integrity: { algorithm: string; stored: string; recomputed: string; verified: boolean; raw_bytes: number };
  field_accounting: {
    parsed_count: number;
    mapped_count: number;
    preserved_count: number;
    unaccounted: string[];
    extensions_not_in_parse: string[];
    fields: AccountedField[];
  };
  nothing_silently_discarded: boolean;
  basis: string[];
}

export interface SourceSummary {
  source_key: string;
  kind: string;
  vendor: string | null;
  product: string | null;
  events: Record<string, number>;
  partial_rate: number | null;
  formats: Record<string, number>;
  adapter_versions_seen: Record<string, number>;
  active_version: string | null;
  baseline: Dict | null;
  drift: Record<string, number>;
  under_review: number;
  learning_sessions: Record<string, number>;
  last_seen: string | null;
}

export interface SourceDetail extends SourceSummary {
  versions: {
    version: number;
    status: string;
    origin: string;
    session_id: string;
    approved_by: string | null;
    approved_at: string | null;
    deactivated_at: string | null;
    match_rate: number | null;
    mapping: Dict;
  }[];
  baseline_history: Dict[];
  recent_drift: (EventRow & { change_types?: string[] | null; review?: Dict | null })[];
}

export interface TimelineEntry {
  at: string;
  phase: string;
  kind: string;
  title: string;
  details: Dict;
  refs: Dict;
}

// Existing Phase 0-6 APIs (partial shapes: only what the UI reads).
export interface UniversalEvent {
  event_id: string;
  raw_event: string;
  raw_hash: string;
  status: string;
  format_detected: string;
  vendor: string | null;
  product: string | null;
  adapter_id: string | null;
  adapter_version: string | null;
  network: Dict | null;
  user: Dict | null;
  process: Dict | null;
  extensions: Dict;
  normalized_event: Dict | null;
  processing_metadata: { drift?: DriftRecord | null } & Dict;
  structural_fingerprint: { field_set?: string[]; field_order?: string[]; field_types?: Record<string, string> } | null;
  warnings: string[];
  error_message: string | null;
  event_action: string | null;
  severity: string | null;
  event_timestamp: string | null;
  ocsf_class_name: string | null;
}

export interface DriftRecord {
  status: string;
  source_key: string;
  baseline_version?: number | null;
  baseline_origin?: string | null;
  matched?: string | null;
  similarity?: number | null;
  threshold?: number | null;
  differences?: {
    added_fields?: string[];
    removed_fields?: string[];
    type_changes?: Record<string, { baseline: string; current: string }>;
    order_changed?: boolean;
    format_changed?: { baseline: string; current: string } | null;
  } | null;
  change_types?: string[];
  severity?: string | null;
  severity_score?: number | null;
  critical_field_changes?: { field: string; target?: string | null; change: string; baseline_type?: string | null; current_type?: string | null }[];
  recommended_actions?: string[];
  recommended_action?: string | null;
  reonboarding_required?: boolean;
  current_adapter?: string | null;
  evidence?: { reason: string; detail?: string | null }[];
  review?: { resolution: string; reviewed_at: string; note?: string | null } | null;
  explanation?: string;
  evaluated_at?: string | null;
}

export interface Baseline {
  source_key: string;
  version: number;
  origin: string;
  fingerprint: { field_set?: string[]; field_order?: string[]; field_count?: number };
  accepted_variants: { fingerprint: { field_set?: string[] } }[];
}

export interface OnboardingSummary {
  id: string;
  name: string | null;
  status: string;
  sample_count: number;
  proposal_version: number;
  validation_result: string | null;
  match_rate: number | null;
  adapter_id: string | null;
  adapter_version: number | null;
  created_at: string;
}

export interface OnboardingSession extends OnboardingSummary {
  samples: { index: number; raw: string; raw_hash: string; source_event_id: string | null }[];
  analysis: Dict & { dominant_format?: string; fields?: Record<string, Dict>; optional_fields?: string[] };
  proposal: (Dict & { vendor?: string; product?: string; format?: string }) | null;
  proposal_source: string | null;
  suggestion_error: { kind: string; message: string; provider?: string } | null;
  validation: (Dict & {
    result?: string;
    reasons?: string[];
    accepted_mappings?: { raw_field: string; target: string; confidence: number; evidence: string; type?: string | null }[];
    rejected_mappings?: { raw_field: string; target: string; reason: string }[];
    metrics?: { total_samples: number; matched_samples: number; failed_samples: number; match_rate: number; mapping_coverage: number; unknown_fields: string[]; warning_count: number; structural_consistency: number } | null;
    thresholds?: Record<string, number>;
  }) | null;
  decisions: Dict[];
  activation: { active: boolean; state: string; eligible_for_approval: boolean; adapter_id: string | null; adapter_version: number | null };
  explanation: string;
}

export interface LearningSummary {
  id: string;
  status: string;
  source_key: string;
  source_adapter_version: number;
  target_version: number | null;
  risk: string;
  learning_modes: string[];
  validation_result: string | null;
  trigger_event_id: string;
  created_at: string;
}

export interface LearningSession {
  id: string;
  status: string;
  source_key: string;
  source_adapter_id: string;
  source_adapter_version: number;
  target_version: number | null;
  target_version_status: string | null;
  trigger_event_id: string;
  drift: DriftRecord;
  evidence: { drifted: { event_id: string; raw_hash: string }[]; historical: { event_id: string; raw_hash: string }[] } & Dict;
  learning_modes: string[];
  risk: string;
  risk_reasons: string[];
  proposal: (Dict & {
    add_mappings?: { raw_field: string; target: string; confidence: string; evidence: string[] }[];
    remaps?: { from_field: string; to_field: string; target: string; confidence: string; evidence: string[] }[];
    remove_mappings?: { raw_field: string; target: string; reason: string }[];
    optional_fields?: string[];
    unresolved?: { field: string; kind: string; reason: string }[];
    notes?: string[];
  }) | null;
  proposal_version: number;
  proposal_source: string | null;
  assistant: (Dict & { name?: string; error?: { kind: string; message: string } | null }) | null;
  mapping_diff: { added: string[]; removed: string[]; changed: string[]; unchanged: string[]; optional_fields: string[] } | null;
  validation: (Dict & {
    result?: string;
    reasons?: string[];
    compatibility_confirmation_required?: boolean;
    new_structure?: { matched_samples: number; total_samples: number; match_rate: number; mapping_coverage: number } | null;
    historical?: { total_samples: number } | null;
    regressions?: { index: number; changed: string[] }[];
  }) | null;
  decisions: { action: string; by?: string | null; note?: string | null; at: string }[];
  recommendation: string;
  report: string;
  approved_by: string | null;
  activated_at: string | null;
  created_at: string;
}

export interface Health {
  status: string;
}

// Demo Mode (/api/v1/demo): fixtures and a read-only progress status derived from stored rows.
export interface DemoFixture {
  key: string;
  kind: string;
  raw: string;
  sha256: string;
  purpose: string;
}

export interface DemoFixtures {
  namespace: { session_name: string; source_key: string; marker: string; operator: string };
  fixtures: DemoFixture[];
}

export type DemoStepState = "done" | "pending" | "human" | "failed" | "waiting";

export interface DemoEventRef {
  event_id: string;
  status: string;
  adapter_id: string | null;
  adapter_version: string | null;
  drift_status: string | null;
  drift_resolution: string | null;
}

export interface DemoStatus {
  namespace: { session_name: string; source_key: string; marker: string };
  exists: boolean;
  conflict: string | null;
  complete: boolean;
  next: { step: string | null; kind: "pending" | "human" | "failed" | "complete"; action: string | null };
  steps: { key: string; title: string; state: DemoStepState; detail: string | null }[];
  onboarding_session: {
    id: string; status: string; proposal_version: number; proposal_source: string | null;
    validation_result: string | null; match_rate: number | null; sample_count: number;
  } | null;
  adapter_versions: { version: number; status: string; session_id: string }[];
  learning_session: {
    id: string; status: string; proposal_version: number; risk: string; validation_result: string | null;
    target_version: number | null; compatibility_confirmation_required: boolean;
  } | null;
  events: Record<string, DemoEventRef>;
}

export interface DemoReset {
  deleted: Record<string, number>;
  non_demo_rows: { before: Record<string, number>; after: Record<string, number>; unchanged: boolean };
}
