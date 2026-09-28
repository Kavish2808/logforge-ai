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
  // Phase 7 (optional: absent from older API responses)
  extension_storage?: string;
  overflow_field_count?: number;
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
  evidence?: LineageEvidence | null;
}

export interface LineageEvidence {
  extension_storage: { mode: string; inline_field_count: number; overflow_field_count: number; overflow_bytes: number; overflow_sha256: string | null };
  raw_storage: RawStorage | null;
  merkle: { batch_id: string; batch_seq: number; leaf_index: number; leaf_hash: string; root_hash: string; chain_hash: string } | null;
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
  candidate?: Dict | null;  // candidate adapter of the current proposal (Phase 8 shadow validation compares it)
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

// --- Phase 7: trust / governance / integration ---------------------------------------------

export interface RawStorage {
  tier: string; status: string; backend: string; object_key: string | null; sha256: string; byte_size: number;
  encoding: string; error: string | null; attempts: number; stored_at: string | null; verified_at: string | null;
}

export interface Me { username: string; role: string | null; authenticated: boolean; capabilities: string[]; rbac_mode: string }
export interface AuthStatus { rbac_mode: string; bootstrap_required: boolean; roles: string[]; capabilities: Record<string, string[]>; app_env?: string; production_safe?: boolean }
export interface User { id: string; username: string; role: string; active: boolean; capabilities: string[]; created_by: string | null; created_at: string }
export interface LoginResult { access_token: string; expires_at: string; user: User }

export interface ReviewSla {
  item_type: string; item_id: string; source_key: string | null; severity: string; opened_at: string; due_at: string;
  sla_hours: number; status: string; review_age_seconds: number; seconds_to_deadline: number; escalation_count: number;
  last_escalated_at: string | null; resolved_at: string | null; resolution: string | null; next_action: string | null; fallback: string | null;
}
export interface Reviews { items: ReviewSla[]; stats: { by_status: Record<string, number>; open_by_type: Record<string, number>; open: number }; policy: Dict }

export interface AuditRecord {
  seq: number; audit_id: string; actor: string; role: string | null; authenticated: boolean; action: string; object_type: string;
  object_id: string | null; decision: string; timestamp: string; details: Dict; evidence_ref: string | null;
  previous_hash: string; current_hash: string;
}
export interface AuditPage { items: AuditRecord[]; stats: { total: number; by_decision: Record<string, number>; head: { seq: number; hash: string; at: string } | null }; next_before_seq: number | null }
export interface ChainProblem { seq: number | string; problem: string; detail?: string; [k: string]: unknown }
export interface AuditVerify { valid: boolean; records_checked: number; head_hash: string | null; first_break_seq: number | null; problems: ChainProblem[]; verified_at: string }

export interface Alert {
  id: string; kind: string; severity: string; title: string; message: string; object_type: string | null; object_id: string | null;
  details: Dict; status: string; read: boolean; occurrences: number; deliveries: { channel: string; ok: boolean; at: string; error?: string }[];
  acknowledged_by: string | null; acknowledged_at: string | null; created_at: string; last_seen_at: string;
}
export interface AlertCounts { by_status: Record<string, number>; unread: number; open_by_severity: Record<string, number>; open_by_kind: Record<string, number> }
export interface AlertPage { total: number; items: Alert[]; counts: AlertCounts }

export interface TrustSummary {
  extension_overflow: {
    events_total: number; events_spilled: number; events_inline: number; overflow_field_count: number; overflow_bytes: number;
    evidence_signatures: number; budget: { max_bytes: number; max_fields: number };
    by_adapter: Record<string, { events: number; fields: number; bytes: number }>; note: string;
  };
  raw_vault: {
    enabled: boolean; backend: Dict; events_total: number; hot: number; cold_stored: number; cold_bytes: number; cold_failed: number;
    not_yet_archived: number; by_tier_status: Record<string, number>;
  };
  integrity: {
    batches: number; events_sealed: number; events_unsealed: number;
    head: { seq: number; batch_id: string; root_hash: string; chain_hash: string; event_count: number; end_time: string; anchored_at: string | null } | null;
  };
  reviews: { by_status: Record<string, number>; open_by_type: Record<string, number>; open: number };
  confidence: { ledger_entries: Record<string, number>; total: number };
  audit: { total: number; by_decision: Record<string, number>; head: { seq: number; hash: string; at: string } | null };
  alerts: AlertCounts;
  exports: { exports: number; by_status: Record<string, number>; rows_exported: number; last_export_at: string | null };
}

export interface ChainVerify {
  valid: boolean; batches_checked: number; events_sealed: number; problems: ChainProblem[];
  sealed_events_since_deleted: number | null; anchor_store: Dict; verified_at: string; head: Dict | null;
}
export interface EventVerify {
  event_id: string; event_present: boolean; valid: boolean; status: string;
  hash?: { stored: string; recomputed: string; valid: boolean };
  cold_copy?: { object_key: string | null; valid: boolean | null; status?: string };
  merkle: {
    sealed: boolean; batch_id?: string; batch_seq?: number; leaf_index?: number; root_hash?: string; inclusion_valid?: boolean;
    anchor_valid?: boolean; leaf_matches_event?: boolean | null; proof?: { side: string; hash: string }[];
  };
}
export interface Batch {
  seq: number; batch_id: string; root_hash: string; prev_chain_hash: string; chain_hash: string; event_count: number;
  start_time: string; end_time: string; anchored_at: string | null;
}
export interface OverflowSignature {
  id: number; adapter_id: string; key_signature: string; keys: string[]; key_count: number; occurrences: number; sample_event_ids: string[];
  first_seen: string; last_seen: string; onboarding_session_id: string | null; onboarding_evidence: boolean; recommended_action: string;
}
export interface ExtensionView {
  event_id: string; mode: string; inline_field_count: number; overflow_field_count: number; overflow_bytes: number;
  overflow_sha256: string | null; overflow_integrity_verified: boolean | null; total_field_count: number; extensions: Dict;
}
export interface RawRecovery {
  event_id: string; recovered: boolean; reason?: string; byte_size?: number; sha256?: string; matches_event_hash?: boolean; matches_hot_copy?: boolean;
}

export interface MutationOperator { kind: string; applicable: boolean; match_rate?: number; detection_rate?: number; mutants?: number }
export interface ConfidenceEntry {
  id: number; subject_type: string; subject_id: string; proposal_version: number; proposal_source: string | null;
  suggestion_confidence: number | null; sample_count: number; created_at: string;
  evidence: {
    suggestion?: Dict;
    in_sample?: { result?: string; match_rate?: number; failed_samples?: number };
    structural?: { fields_observed?: number; fields_mapped?: number; fields_preserved?: number | null; structural_coverage?: number | null; structure_variants?: number };
    holdout?: {
      evaluated?: boolean; reason?: string; kind?: string; split?: { train: number; holdout: number };
      rederived_offline?: { result?: string; holdout?: { match_rate: number; matched_samples: number; total_samples: number } };
      historical?: { match_rate: number; total_samples: number } | null; regressions?: number;
    };
    mutation?: {
      evaluated?: boolean; reason?: string; robustness_survival_rate?: number | null; fault_detection_rate?: number | null;
      operators?: Record<string, MutationOperator>;
    };
    failed_parses?: Dict;
  };
  human_decision: { action: string; by: string | null; at: string } | null;
  production_outcome: { events: number; by_status: Record<string, number>; success_rate: number | null; measurable: boolean; version: number } | null;
}

export interface GovConfig { review_sla: Dict & { hours: Record<string, number> }; alert_thresholds: Record<string, number>; static: Dict }
export interface ExportLog {
  id: string; actor: string; role: string | null; format: string; filters: Dict; max_events: number; include_raw: boolean;
  status: string; rows: number; has_more: boolean | null; started_at: string; completed_at: string | null;
}
export interface PolicyRule { method: string; path: string; action: string; object_type: string; critical: boolean; maker_checker_actions: string[] }
