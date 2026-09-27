// Phase 7 console tests (mocked fetch): trust pages, RBAC sign-in, SLA / confidence
// evidence, overflow indicators. Fixtures live only here.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { clearAuth } from "./lib/auth";

type Handler = (url: URL, init?: RequestInit) => unknown;
let routes: Record<string, Handler>;
let calls: { url: URL; init?: RequestInit }[];

const H = "a".repeat(64);
const trust = {
  extension_overflow: { events_total: 10, events_spilled: 2, events_inline: 8, overflow_field_count: 37, overflow_bytes: 4096,
    evidence_signatures: 1, budget: { max_bytes: 8192, max_fields: 64 }, by_adapter: {}, note: "no storage saving is claimed" },
  raw_vault: { enabled: true, backend: { backend: "filesystem" }, events_total: 10, hot: 10, cold_stored: 9, cold_bytes: 900, cold_failed: 1,
    not_yet_archived: 0, by_tier_status: { "HOT_AND_COLD:STORED": 9, "HOT_ONLY:FAILED": 1 } },
  integrity: { batches: 2, events_sealed: 8, events_unsealed: 2, head: { seq: 2, batch_id: "B2", root_hash: H, chain_hash: H, event_count: 3, end_time: "2026-09-20T10:00:00Z", anchored_at: "2026-09-20T10:00:00Z" } },
  reviews: { by_status: { OVERDUE: 1 }, open_by_type: {}, open: 1 },
  confidence: { ledger_entries: { ONBOARDING: 3 }, total: 3 },
  audit: { total: 12, by_decision: { SUCCESS: 11, DENIED: 1 }, head: { seq: 12, hash: H, at: "2026-09-20T10:00:00Z" } },
  alerts: { by_status: { OPEN: 2 }, unread: 2, open_by_severity: { CRITICAL: 1, HIGH: 1 }, open_by_kind: {} },
  exports: { exports: 1, by_status: { COMPLETED: 1 }, rows_exported: 5, last_export_at: null },
};
const alert = {
  id: "01ALERT", kind: "INTEGRITY_FAILURE", severity: "CRITICAL", title: "Evidence chain verification failed", message: "ROOT_MISMATCH",
  object_type: null, object_id: null, details: {}, status: "OPEN", read: false, occurrences: 1,
  deliveries: [{ channel: "internal", ok: true, at: "2026-09-20T10:00:00Z" }, { channel: "webhook", ok: false, at: "2026-09-20T10:00:00Z", error: "URLError" }],
  acknowledged_by: null, acknowledged_at: null, created_at: "2026-09-20T10:00:00Z", last_seen_at: "2026-09-20T10:00:00Z",
};
const auditRow = (seq: number, over: Record<string, unknown> = {}) => ({
  seq, audit_id: `A${seq}`, actor: "eng2", role: "SECURITY_ENGINEER", authenticated: true, action: "ONBOARDING_APPROVE", object_type: "onboarding_session",
  object_id: "01ONB", decision: "SUCCESS", timestamp: "2026-09-20T10:00:00Z", details: { maker_checker: { makers: ["eng1"] } }, evidence_ref: null,
  previous_hash: H, current_hash: H, ...over,
});
const sla = { item_type: "ONBOARDING_SESSION", item_id: "01ONB", source_key: "acme_fw", severity: "MEDIUM", opened_at: "2026-09-20T10:00:00Z",
  due_at: "2026-09-23T10:00:00Z", sla_hours: 72, status: "ESCALATED", review_age_seconds: 400000, seconds_to_deadline: -140000, escalation_count: 2,
  last_escalated_at: null, resolved_at: null, resolution: null, next_action: "ESCALATED to SOC_ADMIN — SECURITY_ENGINEER: approve or reject",
  fallback: "Nothing is activated on timeout" };
const confidence = { entries: [{ id: 1, subject_type: "ONBOARDING", subject_id: "01ONB", proposal_version: 1, proposal_source: "offline",
  suggestion_confidence: 0.84, sample_count: 12, created_at: "2026-09-20T10:00:00Z",
  evidence: { in_sample: { result: "PASSED", match_rate: 1 }, structural: { fields_observed: 10, fields_mapped: 7, fields_preserved: 3, structural_coverage: 0.7 },
    holdout: { evaluated: true, split: { train: 9, holdout: 3 }, rederived_offline: { result: "EVALUATED", holdout: { match_rate: 1, matched_samples: 3, total_samples: 3 } } },
    mutation: { evaluated: true, robustness_survival_rate: 0.67, fault_detection_rate: 1, operators: { truncate_half: { kind: "fault", applicable: true, detection_rate: 1, mutants: 12 } } } },
  human_decision: null, production_outcome: null }], note: "" };
const onboarding = {
  id: "01ONB", name: "acme", status: "VALIDATED", sample_count: 12, proposal_version: 1, validation_result: "PASSED", match_rate: 1,
  adapter_id: null, adapter_version: null, created_at: "2026-09-20T10:00:00Z", samples: [], analysis: { fields: {} }, proposal: { vendor: "Acme" },
  proposal_source: "offline", suggestion_error: null, validation: { result: "PASSED", reasons: [], accepted_mappings: [], rejected_mappings: [], metrics: null },
  decisions: [], activation: { active: false, state: "NOT_ACTIVE_AWAITING_APPROVAL", eligible_for_approval: true, adapter_id: null, adapter_version: null },
  explanation: "",
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push({ url, init });
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const handler = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    if (!handler) return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: `no mock for ${path}` } }), { status: 404 });
    const result = handler(url, init);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

function go(hash: string) {
  window.location.hash = hash;
  return render(<App />);
}

beforeEach(() => {
  calls = [];
  clearAuth();
  routes = {
    "/health": () => ({ status: "ok" }),
    "/views/summary": () => ({ window: {}, totals: { pending_reviews: 0 }, by_status: {}, by_format: {}, by_vendor: {}, by_adapter: {},
      by_drift_status: {}, by_drift_severity: {}, adapters: {}, onboarding_sessions: {}, learning_sessions: {}, trend: [] }),
    "/views/trust": () => trust,
    "/alerts/counts": () => trust.alerts,
    "/integrity/batches": () => ({ items: [{ seq: 2, batch_id: "B2", root_hash: H, prev_chain_hash: H, chain_hash: H, event_count: 3,
      start_time: "2026-09-20T10:00:00Z", end_time: "2026-09-20T10:00:00Z", anchored_at: "2026-09-20T10:00:00Z" }] }),
    "/integrity/overflow/evidence": () => ({ items: [{ id: 7, adapter_id: "json_generic", key_signature: H, keys: ["k1", "k2"], key_count: 2,
      occurrences: 5, sample_event_ids: [], first_seen: "2026-09-20T10:00:00Z", last_seen: "2026-09-20T10:00:00Z", onboarding_session_id: null,
      onboarding_evidence: true, recommended_action: "" }] }),
    "/integrity/verify": () => ({ valid: false, batches_checked: 2, events_sealed: 8, problems: [{ seq: 2, problem: "ANCHOR_MISMATCH" }],
      sealed_events_since_deleted: 0, anchor_store: {}, verified_at: "2026-09-20T10:00:00Z", head: null }),
    "/alerts": () => ({ total: 1, items: [alert], counts: trust.alerts }),
    "/alerts/channels": () => ({ channels: [{ channel: "internal", configured: true }, { channel: "webhook", configured: false }] }),
    "POST /alerts/01ALERT/ack": () => ({ ...alert, status: "ACKNOWLEDGED", acknowledged_by: "anonymous" }),
    "/governance/audit": () => ({ items: [auditRow(2), auditRow(1, { decision: "DENIED", actor: "eng1" })],
      stats: { total: 2, by_decision: { SUCCESS: 1, DENIED: 1 }, head: { seq: 2, hash: H, at: "2026-09-20T10:00:00Z" } }, next_before_seq: null }),
    "/governance/audit/verify": () => ({ valid: false, records_checked: 2, head_hash: H, first_break_seq: 2,
      problems: [{ seq: 2, problem: "CONTENT_MODIFIED", detail: "record content no longer matches its hash" }], verified_at: "2026-09-20T10:00:00Z" }),
    "/auth/status": () => ({ rbac_mode: "permissive", bootstrap_required: false, roles: [], capabilities: {}, app_env: "development", production_safe: false }),
    "/governance/reviews": () => ({ items: [sla], stats: { by_status: { ESCALATED: 1 }, open_by_type: {}, open: 1 }, policy: {} }),
    "/governance/config": () => ({ review_sla: { hours: { CRITICAL: 4, HIGH: 24, MEDIUM: 72, LOW: 168 } }, alert_thresholds: { parser_failure_rate: 0.5 }, static: { rbac_mode: "permissive" } }),
    "/governance/policy": () => ({ rbac_mode: "permissive", roles: {}, rules: [{ method: "POST", path: "/api/v1/onboarding/sessions/{session_id}/approve",
      action: "ONBOARDING_APPROVE", object_type: "onboarding_session", critical: true, maker_checker_actions: [] }] }),
    "POST /auth/login": () => ({ access_token: "tok-123", expires_at: "2026-09-21T10:00:00Z",
      user: { id: "U", username: "eng2", role: "SECURITY_ENGINEER", active: true, capabilities: ["inspect", "approve_adapter"], created_by: null, created_at: "" } }),
    "/onboarding/sessions/01ONB": () => onboarding,
    "/confidence/onboarding/01ONB": () => confidence,
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
  clearAuth();
});

describe("phase 7 routes render", () => {
  it.each([["#/integrity", "Integrity"], ["#/alerts", "Alerts"], ["#/audit", "Audit log"], ["#/governance", "Governance"]])("%s", async (hash, heading) => {
    go(hash);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeTruthy();
  });
});

describe("integrity", () => {
  it("shows hot/cold, sealing and overflow numbers from the API and reports chain verification", async () => {
    go("#/integrity");
    expect(await screen.findByText("Raw: cold (vault)")).toBeTruthy();
    expect(screen.getByText("Spilled events")).toBeTruthy();
    expect(screen.getByText(/37 fields · 4,096 bytes in overflow/)).toBeTruthy();
    expect(await screen.findByText("onboarding evidence")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText(/Chain verification FAILED/)).toBeTruthy();
    expect(screen.getByText(/ANCHOR_MISMATCH/)).toBeTruthy();
  });
});

describe("alerts and audit", () => {
  it("lists alerts with failed deliveries and acknowledges", async () => {
    go("#/alerts");
    expect(await screen.findByText("Evidence chain verification failed")).toBeTruthy();
    expect(screen.getByText("webhook failed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST" && c.url.pathname.endsWith("/alerts/01ALERT/ack"))).toBe(true));
  });

  it("shows audit records and flags a broken chain", async () => {
    go("#/audit");
    expect(await screen.findByText("eng1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText(/Audit chain BROKEN at record 2/)).toBeTruthy();
    expect(screen.getByText("chain break")).toBeTruthy();
  });
});

describe("rbac", () => {
  it("signs in and sends the bearer token on later requests", async () => {
    go("#/governance");
    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "eng2" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Signed in")).toBeTruthy();
    expect(screen.queryByText(/Development \/ demo mode/)).toBeNull();
    const login = calls.find((c) => c.url.pathname.endsWith("/auth/login"))!;
    expect((login.init?.headers as Record<string, string> | undefined)?.Authorization).toBeUndefined();
    calls = [];
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-123");
  });

  it("warns that permissive RBAC is not production-safe", async () => {
    go("#/governance");
    expect(await screen.findByText(/Development \/ demo mode/)).toBeTruthy();
  });

  it("explains anonymous mode on approval screens", async () => {
    go("#/onboarding/01ONB");
    expect(await screen.findByText(/audited as/)).toBeTruthy();
  });
});

describe("review SLA and confidence evidence", () => {
  it("shows the SLA and confidence ledger on an onboarding session", async () => {
    go("#/onboarding/01ONB");
    expect(await screen.findByText("Review SLA")).toBeTruthy();
    expect(screen.getByText(/escalated ×2/)).toBeTruthy();
    expect(screen.getByText(/ESCALATED to SOC_ADMIN/)).toBeTruthy();
    expect(await screen.findByText("Confidence evidence")).toBeTruthy();
    expect(screen.getByText("Mutation survival")).toBeTruthy();
    expect(screen.getByText("67%")).toBeTruthy();
    expect(screen.getByText(/not a statistical calibration/)).toBeTruthy();
  });
});

describe("overflow indicators", () => {
  it("marks spilled events in the event table", async () => {
    routes["/views/filters"] = () => ({ statuses: [], formats: [], vendors: [], products: [], sources: [], adapters: [], categories: [], severities: [], drift_statuses: [], drift_severities: [] });
    routes["/views/events"] = () => ({ items: [{ event_id: "01E", received_at: "2026-09-20T10:00:00Z", event_timestamp: null, status: "SUCCESS", format_detected: "json",
      vendor: null, product: null, source_key: "json_generic", adapter_id: "json_generic", adapter_version: "1", adapter_source: "manual",
      ocsf_class_name: null, ocsf_category_name: null, event_type: null, event_action: null, severity: null, drift_status: null, drift_severity: null,
      warning_count: 0, preserved_field_count: 40, raw_hash: H, extension_storage: "SPILLED", overflow_field_count: 35 }], limit: 50, next_cursor: null, has_more: false, total: 1 });
    go("#/events");
    expect(await screen.findByText("SPILLED")).toBeTruthy();
  });

  it("renders the trust card on the overview only when the API provides it", async () => {
    go("#/overview");
    expect(await screen.findByText("Trust & governance")).toBeTruthy();
    cleanup();
    delete routes["/views/trust"];
    go("#/overview");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeTruthy();
    expect(screen.queryByText("Trust & governance")).toBeNull();
  });
});
