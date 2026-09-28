// Phase 8 console tests (mocked fetch): advanced drift, baseline integrity,
// shadow validation, review queue, replay / revisions, compact lineage and
// correlation. Fixtures mirror the backend response shapes and live only here.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { LearningSession } from "./api/types";
import { CompactLineageCard, RevisionsCard } from "./components/forensics";
import { clearAuth } from "./lib/auth";
import { ShadowPanel } from "./pages/ShadowValidation";

type Handler = (url: URL, init?: RequestInit) => unknown;
let routes: Record<string, Handler>;
let calls: { url: URL; init?: RequestInit }[];

const H = "b".repeat(64);
const WINDOW = { baseline_start: "2026-09-01T00:00:00Z", baseline_end: "2026-09-08T00:00:00Z",
  current_start: "2026-09-08T00:00:00Z", current_end: "2026-09-08T01:00:00Z" };
const finding = (over: Record<string, unknown> = {}) => ({
  id: "F1", layer: "STATISTICAL", source: "fw_a", field: "event_action", metric: "PSI", baseline_value: { distribution: { allow: 200 } },
  current_value: { distribution: { blocked: 300 } }, deviation: 0.91, threshold: 0.25, severity: "HIGH",
  deterministic_reason: "PSI_AT_OR_ABOVE_THRESHOLD", explanation: "Population stability index of 'event_action' is 0.91.",
  advisory: false, quality: "LOW", evidence_counts: { baseline_events: 400, current_events: 300 }, analysis_window: WINDOW,
  parent_finding_id: null, status: "OPEN", acknowledged_by: null, ...over,
});
const golden = { id: "G1", source_key: "fw_a", version: 1, status: "ACTIVE", fingerprint: { reference: { field_order: ["a"] } },
  statistical_profile: { n: 12, sufficient: false, min_sample: 200 }, derived_from_baseline_version: 1, approved_by: "admin",
  approved_role: "SOC_ADMIN", note: "trusted", evidence: { adapter_id: "fw_a", adapter_version: "1" }, created_at: "2026-09-01T00:00:00Z" };
const policy = { golden_similarity_threshold: 0.7, max_changes_since_golden: 5, guarded_actions: ["DRIFT_ADD_VARIANT"],
  elevated_requires: "authenticated SOC_ADMIN + non-empty note (+ Phase 7 maker-checker)" };
const current = { source_key: "fw_a", adapter_id: "fw_a", adapter_version: "1", format_detected: "kv", origin: "human_review", version: 3,
  fingerprint: { field_order: ["a", "b"], field_count: 2 }, accepted_variants: [{ fingerprint: {} }], created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z", under_review_count: 0 };
const job = (over: Record<string, unknown> = {}) => ({
  id: "J1", trigger: "MANUAL", source: "fw_a", adapter_id: "fw_a", selection: { from_version: "*", window_start: null, window_end: null,
    selection_cutoff: "2026-09-08T00:00:00Z" }, target_version: "1", status: "RUNNING", total: 10, processed: 4, succeeded: 4, failed: 0,
  skipped: 0, rate_per_sec: 50, batch_size: 100, reason: "mapping fix", created_by: "eng1", approved_by: null, error: null,
  created_at: "2026-09-08T00:00:00Z", started_at: "2026-09-08T00:01:00Z", completed_at: null, updated_at: "2026-09-08T00:02:00Z",
  checkpoint: { received_at: "2026-09-07T10:00:00Z", event_id: "E4" }, large_replay: false,
  integrity: { raw_hash_mismatches: 0, revisions_written: 4, merkle: null }, errors: [], slices: 2, ...over,
});
const correlation = {
  id: "C1", window: { start: "2026-09-08T00:00:00Z", end: "2026-09-08T01:00:00Z" }, sources: ["fw_a", "fw_b"], vendors: ["VendorA", "VendorB"],
  drift_types: ["STATISTICAL", "STRUCTURAL"], affected_fields: ["severity"], change_types: ["FIELD_REMOVAL", "PSI"],
  drift_finding_ids: ["F1"], event_ids: ["E9"], score: 0.8, strength: "HIGH",
  score_breakdown: { source_diversity: { value: 0.3333, weight: 0.3, contribution: 0.1 }, change_overlap: { value: 1, weight: 0.25, contribution: 0.25 },
    shared_fields: { value: 1, weight: 0.3, contribution: 0.3 }, time_proximity: { value: 1, weight: 0.15, contribution: 0.15 } },
  per_source: { fw_a: { vendor: "VendorA", fields: ["severity"], change_types: ["FIELD_REMOVAL"], kinds: ["STRUCTURAL"], first_seen: "2026-09-08T00:10:00Z", evidence: 1 },
    fw_b: { vendor: "VendorB", fields: ["severity"], change_types: ["PSI"], kinds: ["STATISTICAL"], first_seen: "2026-09-08T00:12:00Z", evidence: 1 } },
  explanation: "2 vendors (VendorA, VendorB) across 2 sources drifted. Investigation aid only: nothing was changed.",
  status: "OPEN", created_at: "2026-09-08T01:00:00Z", investigation_only: true,
};
const strata = (sufficient: boolean) => Object.fromEntries(["FAILED", "DRIFT", "PARTIAL_WARNING", "EXTENSION_HEAVY", "NORMAL", "DIVERSITY"].map((n) => [n, {
  target: 25, available: sufficient ? 40 : 3, selected: Array.from({ length: sufficient ? 25 : 3 }, (_, i) => `${n}${i}`), sufficient,
  results: { events: sufficient ? 25 : 3, old_parse_success: 3, new_parse_success: 3, critical: 0, review: 0, improvements: 1, unchanged: 2 } }]));
const shadowRun = (verdict: string, over: Record<string, unknown> = {}) => ({
  id: `R-${verdict}`, learning_session_id: "LS1", source: "fw_a", proposal_version: 2, current_version: "1", candidate_version: "2",
  status: verdict, verdict, breaker_tripped: verdict === "BLOCKED", sample_count: 18,
  reasons: verdict === "BLOCKED" ? [{ code: "EVIDENCE_LOSS", critical: true, count: 2, event_ids: ["E1"], detail: "fields accounted for today would no longer be accounted for" }]
    : verdict === "REVIEW_REQUIRED" ? [{ code: "INSUFFICIENT_COVERAGE", critical: false, detail: "some strata have fewer events than the target" }] : [],
  strata: strata(verdict === "PASSED"),
  summary: { status: verdict, started_at: "2026-09-08T00:00:00Z", finished_at: "2026-09-08T00:00:01Z", duration_ms: 230,
    totals: { status_changes: 0, evidence_loss: verdict === "BLOCKED" ? 2 : 0, raw_hash_mismatches: 0, drift_differences: 0,
      old_parse_success: 18, new_parse_success: 18, review_differences: 0, improvements: 1 } },
  latency: { old: { p50: 0.1, p95: 0.19 }, new: { p50: 0.1, p95: 0.17 }, repeats_per_event: 3, unit: "ms" },
  thresholds: { policy: "BLOCK_CRITICAL", stratum_target: 25, latency_ratio: 3, latency_floor_ms: 0.5 },
  created_by: "analyst", created_at: "2026-09-08T00:00:00Z",
  diff: [{ event_id: "E1", stratum: "DRIFT", raw_hash: H, old: { status: "SUCCESS", adapter: "fw_a", version: "1", drift: "DRIFT" },
    new: { status: "SUCCESS", adapter: "fw_a", version: "2", drift: "DRIFT" }, changes: [{ kind: "NEWLY_MAPPED", target: "user.name", new: "bob" }] }],
  ...over,
});
const session = { id: "LS1", status: "APPROVED", proposal_version: 2, candidate: { id: "fw_a" } } as unknown as LearningSession;

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push({ url, init });
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const handler = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    if (!handler) return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: `no mock for ${path}` } }), { status: 404 });
    const result = await handler(url, init);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

const fail = (status: number, message: string) =>
  new Response(JSON.stringify({ error: { code: "X", message } }), { status, headers: { "Content-Type": "application/json" } });
const posted = (method: string, path: string) =>
  calls.filter((c) => (c.init?.method ?? "GET") === method && c.url.pathname.endsWith(path));
const bodyOf = (c: { init?: RequestInit }) => JSON.parse(String(c.init?.body ?? "{}"));

function go(hash: string) {
  window.location.hash = hash;
  return render(<App />);
}

beforeEach(() => {
  calls = [];
  clearAuth();
  routes = {
    "/health": () => ({ status: "ok" }),
    "/views/summary": () => ({ totals: { pending_reviews: 0 }, trend: [] }),
    "/alerts/counts": () => ({ by_status: {}, unread: 0, open_by_severity: {}, open_by_kind: {} }),
    "/golden-baselines": () => ({ items: [golden], total: 1, policy }),
    "/drift/findings": () => ({ items: [], stats: { by_layer_status: {}, total: 0 } }),
    "/views/events": () => ({ items: [], next_cursor: null }),
    "/drift/correlations": () => ({ items: [] }),
    "/replay/jobs": () => ({ items: [] }),
    "/golden-baselines/comparisons": () => ({ items: [] }),
    "/drift/baselines": () => ({ total: 0, items: [] }),
  };
  vi.stubGlobal("fetch", mockFetch());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("navigation", () => {
  it.each([["#/advanced-drift", "Advanced Drift"], ["#/baselines", "Baseline Integrity"],
    ["#/correlations", "Cross-vendor correlation"], ["#/replay", "Replay & Revisions"]])("%s renders", async (hash, heading) => {
    go(hash);
    expect(await screen.findByRole("heading", { level: 1, name: new RegExp(heading) })).toBeTruthy();
    expect(screen.getByRole("link", { current: "page" })).toBeTruthy();
  });
});

describe("loading / empty / error states", () => {
  it("shows a loading state while the API is pending", async () => {
    routes["/drift/correlations"] = () => new Promise(() => {});
    go("#/correlations");
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
  });

  it("shows honest empty states", async () => {
    go("#/advanced-drift");
    expect(await screen.findByText(/No statistical findings/)).toBeTruthy();
    cleanup();
    go("#/correlations");
    expect(await screen.findByText(/needs related drift from at least two vendors/)).toBeTruthy();
    cleanup();
    go("#/replay");
    expect(await screen.findByText("No replay jobs yet.")).toBeTruthy();
  });

  it("shows the API error with a retry", async () => {
    routes["/replay/jobs"] = () => fail(500, "database unavailable");
    go("#/replay");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("database unavailable")).toBeTruthy();
    routes["/replay/jobs"] = () => ({ items: [job()] });
    fireEvent.click(within(alert).getByText("Retry"));
    expect(await screen.findByText("fw_a")).toBeTruthy();
  });
});

describe("advanced drift", () => {
  it("lists findings with severity, window, evidence and golden relationship", async () => {
    routes["/drift/findings"] = () => ({ items: [finding()], stats: { by_layer_status: { "STATISTICAL:OPEN": 1 }, total: 1 } });
    routes["/drift/findings/F1"] = () => ({ ...finding(), children: [finding({ id: "S1", layer: "SEMANTIC", metric: "ACTION_RELABEL_ADVISORY",
      severity: "ADVISORY", explanation: "possible relabel 'deny' -> 'blocked'" })] });
    go("#/advanced-drift");
    fireEvent.click(await screen.findByLabelText("Finding PSI on event_action"));
    expect(await screen.findByText("PSI_AT_OR_ABOVE_THRESHOLD")).toBeTruthy();
    expect(screen.getByText("400 baseline · 300 current events")).toBeTruthy();
    expect(screen.getByText(/possible relabel/)).toBeTruthy();
    expect(screen.getAllByText("golden v1", { exact: false }).length).toBeGreaterThan(0);
  });

  it("acknowledging needs an explicit confirmation", async () => {
    routes["/drift/findings"] = () => ({ items: [finding()], stats: { by_layer_status: {}, total: 1 } });
    routes["/drift/findings/F1"] = () => finding();
    routes["POST /drift/findings/F1/acknowledge"] = () => finding({ status: "ACKNOWLEDGED" });
    go("#/advanced-drift");
    fireEvent.click(await screen.findByLabelText("Finding PSI on event_action"));
    fireEvent.click(await screen.findByText("Acknowledge finding"));
    expect(posted("POST", "/acknowledge")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Review note"), { target: { value: "vendor renamed actions" } });
    fireEvent.click(screen.getByText("Confirm acknowledge finding"));
    await waitFor(() => expect(posted("POST", "/acknowledge")).toHaveLength(1));
    expect(bodyOf(posted("POST", "/acknowledge")[0])).toEqual({ note: "vendor renamed actions" });
  });
});

describe("baseline integrity", () => {
  beforeEach(() => {
    routes["/drift/baselines"] = () => ({ total: 1, items: [current] });
    routes["/drift/baselines/fw_a"] = () => current;
    routes["/golden-baselines/fw_b"] = () => fail(404, "Source 'fw_b' has no golden baseline.");
  });

  it("shows current vs golden, the comparison and the elevated notice", async () => {
    routes["/golden-baselines/fw_a"] = () => ({ source_key: "fw_a", active: golden, versions: [golden] });
    routes["/golden-baselines/fw_a/compare"] = () => ({ source_key: "fw_a", golden: { id: "G1", version: 1 }, current_baseline_version: 3,
      structural: { similarity: 0.62, matched: "reference", components: {} }, steps_since_golden: 6,
      changes_since_golden: [{ version: 2, action: "VARIANT_ADDED", event_id: "E2", note: "ok", created_at: "2026-09-02T00:00:00Z" }],
      statistical: { compared: false, reason: "Statistical comparison needs >= 200 events" }, next_change_would_be_elevated: true,
      reasons: ["GOLDEN_SIMILARITY_BELOW_THRESHOLD", "TOO_MANY_CHANGES_SINCE_GOLDEN"], explanation: ["Golden v1 was pinned from Phase 5 baseline v1 by admin."] });
    go("#/baselines/fw_a");
    expect(await screen.findByText(/will require/)).toBeTruthy();
    expect(screen.getByText("62.0% (threshold 70%)").className).toContain("b-fail");
    expect(screen.getByText("trusted")).toBeTruthy();
    expect(screen.getByText("Re-pin golden baseline")).toBeTruthy();
    expect(screen.queryByText("Pin golden baseline")).toBeNull();
  });

  it("pinning requires confirmation and a note, and surfaces the backend's refusal", async () => {
    routes["/golden-baselines/fw_a"] = () => fail(404, "Source 'fw_a' has no golden baseline.");
    routes["POST /golden-baselines/fw_a"] = () => fail(401, "Golden baselines can only be changed by an authenticated SOC_ADMIN.");
    go("#/baselines/fw_a");
    expect(await screen.findByText(/has ever been pinned/)).toBeTruthy();
    fireEvent.click(await screen.findByText("Pin golden baseline"));
    const confirm = screen.getByText("Confirm pin golden baseline") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);  // note required
    fireEvent.change(screen.getByLabelText("Why is this the trusted reference?"), { target: { value: "verified" } });
    expect(confirm.disabled).toBe(false);
    expect(posted("POST", "/golden-baselines/fw_a")).toHaveLength(0);
    fireEvent.click(confirm);
    expect(await screen.findByText("Golden baselines can only be changed by an authenticated SOC_ADMIN.")).toBeTruthy();
    expect(bodyOf(posted("POST", "/golden-baselines/fw_a")[0])).toEqual({ note: "verified" });
  });

  it("retire sends the expected version after confirmation", async () => {
    routes["/golden-baselines/fw_a"] = () => ({ source_key: "fw_a", active: golden, versions: [golden] });
    routes["/golden-baselines/fw_a/compare"] = () => fail(500, "compare failed");
    routes["POST /golden-baselines/fw_a/retire"] = () => ({ ...golden, status: "RETIRED" });
    go("#/baselines/fw_a");
    fireEvent.click(await screen.findByText("Retire golden baseline"));
    fireEvent.change(screen.getByLabelText("Reason for retiring"), { target: { value: "decommissioned" } });
    fireEvent.click(screen.getByText("Confirm retire golden baseline"));
    await waitFor(() => expect(posted("POST", "/retire")).toHaveLength(1));
    expect(bodyOf(posted("POST", "/retire")[0])).toEqual({ note: "decommissioned", expected_version: 1 });
  });
});

describe("shadow validation", () => {
  it.each([["PASSED", "ok"], ["REVIEW_REQUIRED", "warn"], ["BLOCKED", "fail"]])("%s is visually distinct", async (verdict, cls) => {
    routes["/shadow/runs"] = () => ({ items: [shadowRun(verdict)] });
    routes[`/shadow/runs/R-${verdict}`] = () => shadowRun(verdict);
    const { container } = render(<ShadowPanel session={session} />);
    await screen.findAllByText(/Activation eligibility/);
    const banner = container.querySelector(`[data-verdict="${verdict}"]`)!;
    expect(banner.className).toContain(cls);
    expect(screen.getByText("Stratum coverage")).toBeTruthy();
    expect(screen.getAllByText("18/18")).toHaveLength(2);  // parse success OLD and NEW, from totals
    if (verdict === "BLOCKED") {
      expect(screen.getByText(/Activation will be refused by the server/)).toBeTruthy();
      expect(screen.getByText("EVIDENCE LOSS")).toBeTruthy();
    }
    if (verdict === "REVIEW_REQUIRED") {
      expect(screen.getAllByText("insufficient").length).toBe(6);
      expect(screen.getByText(/authenticated SOC_ADMIN with a written note/)).toBeTruthy();
    }
    if (verdict === "PASSED") expect(screen.getByText(/permits activation/)).toBeTruthy();
    expect(screen.getByText("✓ unchanged")).toBeTruthy();  // raw hash result
  });

  it("a run for an older proposal is shown as not gating", async () => {
    routes["/shadow/runs"] = () => ({ items: [shadowRun("BLOCKED", { proposal_version: 1 })] });
    routes["/shadow/runs/R-BLOCKED"] = () => shadowRun("BLOCKED", { proposal_version: 1 });
    render(<ShadowPanel session={session} />);
    expect(await screen.findByText(/it does not gate activation/)).toBeTruthy();
  });

  it("running a shadow validation needs confirmation", async () => {
    routes["/shadow/runs"] = () => ({ items: [] });
    routes["POST /shadow/runs"] = () => shadowRun("PASSED");
    routes["/shadow/runs/R-PASSED"] = () => shadowRun("PASSED");
    render(<ShadowPanel session={session} />);
    expect(await screen.findByText("No shadow run yet for this session.")).toBeTruthy();
    fireEvent.click(screen.getByText("Run shadow validation"));
    expect(posted("POST", "/shadow/runs")).toHaveLength(0);
    fireEvent.click(screen.getByText("Confirm run shadow validation"));
    await waitFor(() => expect(posted("POST", "/shadow/runs")).toHaveLength(1));
    expect(bodyOf(posted("POST", "/shadow/runs")[0])).toEqual({ learning_session_id: "LS1" });
  });
});

describe("replay and forensics", () => {
  it("shows progress, checkpoint and confirms cancellation", async () => {
    routes["/replay/jobs"] = () => ({ items: [job()] });
    routes["/replay/jobs/J1"] = () => job();
    routes["POST /replay/jobs/J1/cancel"] = () => job({ status: "CANCELLED" });
    go("#/replay/J1");
    const bar = await screen.findByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("4");
    expect(screen.getByText(/E4/)).toBeTruthy();
    expect(screen.getByText("✓ raw SHA-256 preserved")).toBeTruthy();
    fireEvent.click(screen.getByText("Cancel"));
    expect(posted("POST", "/cancel")).toHaveLength(0);
    fireEvent.click(screen.getByText("Confirm cancel"));
    await waitFor(() => expect(posted("POST", "/replay/jobs/J1/cancel")).toHaveLength(1));
  });

  it("a large pending replay explains the maker-checker start rule", async () => {
    routes["/replay/jobs"] = () => ({ items: [job({ status: "PENDING_APPROVAL", total: 10001, large_replay: true })] });
    routes["/replay/jobs/J1"] = () => job({ status: "PENDING_APPROVAL", total: 10001, large_replay: true });
    go("#/replay/J1");
    fireEvent.click(await screen.findByText("Start replay"));
    expect(screen.getByText(/other than its creator \(eng1\)/)).toBeTruthy();
  });

  it("rollback requires a reason before it can be confirmed", async () => {
    go("#/replay");
    fireEvent.change(await screen.findByLabelText("Rollback adapter id"), { target: { value: "acme" } });
    fireEvent.click(screen.getByText("Roll back adapter"));
    expect((screen.getByText("Confirm roll back adapter") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders the revision lineage with integrity checks", async () => {
    const rev = (n: number, trigger: string, current: boolean, parent: string | null) => ({
      id: `V${n}`, event_id: "E1", revision_no: n, parent_revision_id: parent, is_current: current, trigger, reason: "r", actor: "system",
      adapter_id: "fw_a", adapter_version: String(n), replay_job_id: trigger === "REPLAY" ? "J1" : null, resulting_status: "SUCCESS",
      processed_at: null, raw_hash: H, snapshot_sha256: H, snapshot_verified: true, raw_hash_matches_event: true,
      created_at: "2026-09-08T00:00:00Z", snapshot: { status: "SUCCESS" } });
    routes["/revisions/E1"] = () => ({ event_id: "E1", event_present: true, raw_hash: H, count: 2, current_revision: 2, note: null,
      revisions: [rev(1, "ORIGINAL", false, null), rev(2, "REPLAY", true, "V1")],
      integrity: { all_snapshots_verified: true, raw_hash_unchanged: true, parent_chain_intact: true, single_current: true } });
    render(<RevisionsCard eventId="E1" />);
    const chain = await screen.findByLabelText("Revision lineage");
    expect(within(chain).getAllByRole("listitem")).toHaveLength(2);
    expect(within(chain).getByText("ORIGINAL")).toBeTruthy();
    expect(within(chain).getByText("REPLAY")).toBeTruthy();
    expect(screen.getByText("✓ raw SHA-256 unchanged")).toBeTruthy();
    expect(screen.getByText("revision 1")).toBeTruthy();
  });

  it("renders compact lineage stages and exceptions, and decode errors", async () => {
    const stages = ["RAW", "FORMAT", "PARSER", "ADAPTER", "NORMALIZATION", "FIELD_ACCOUNTING", "WARNINGS", "DRIFT", "BASELINE"]
      .map((s) => ({ stage: s, outcome: s === "DRIFT" ? "WARN" : "OK" }));
    routes["/lineage/compact/E1"] = () => ({ event_id: "E1", persisted: true, template_version: 1, stage_mask: 1, exception_mask: 4,
      is_exception_column: true, computed_at: "2026-09-08T00:00:00Z", decodable: true, stages, exceptions: ["DRIFT", "REPLAY"],
      is_exception: true, worst_outcome: "WARN", packed_hex: "0101555504", detailed_lineage: "/x", not_in_compact_form: ["summaries"],
      verification: { equivalent: true, stale: false, stage_differences: [], derived_exceptions_now: ["DRIFT"] } });
    render(<CompactLineageCard eventId="E1" />);
    const strip = await screen.findByLabelText("Compact lineage stages");
    expect(within(strip).getAllByRole("listitem")).toHaveLength(9);
    expect(screen.getByText("0101555504")).toBeTruthy();
    expect(screen.getByText("✓ matches detailed lineage")).toBeTruthy();
    cleanup();
    routes["/lineage/compact/E2"] = () => ({ event_id: "E2", persisted: true, template_version: 2, stage_mask: 0, exception_mask: 0,
      is_exception_column: false, computed_at: "2026-09-08T00:00:00Z", decodable: false, error: "Template version mismatch",
      detailed_lineage: "/x", not_in_compact_form: [] });
    render(<CompactLineageCard eventId="E2" />);
    expect(await screen.findByText(/Template version mismatch/)).toBeTruthy();
  });
});

describe("correlation and review queue", () => {
  it("is labelled as investigation and shows the score breakdown", async () => {
    routes["/drift/correlations"] = () => ({ items: [correlation] });
    routes["/drift/correlations/C1"] = () => correlation;
    go("#/correlations/C1");
    expect((await screen.findAllByText(/Investigation/)).length).toBeGreaterThan(1);
    expect(await screen.findByText("Score breakdown")).toBeTruthy();
    expect(screen.getByText("source diversity")).toBeTruthy();
    const total = screen.getByText("Total score").closest("tr")!;
    expect(within(total).getByText("0.8")).toBeTruthy();  // the backend total, not recomputed
    expect(screen.getByText(/does not modify adapters/)).toBeTruthy();
    expect(screen.getAllByText("HIGH correlation").length).toBeGreaterThan(0);
  });

  it("the drift queue lists Phase 8 review items", async () => {
    routes["/drift/findings"] = () => ({ items: [finding()], stats: { by_layer_status: {}, total: 1 } });
    routes["/golden-baselines/comparisons"] = () => ({ items: [{ id: "X1", source_key: "fw_a", action: "DRIFT_ADD_VARIANT", object_type: "event",
      object_id: "E1", decision: "ELEVATED", poisoning_risk: true, risk_reasons: [{ code: "TOO_MANY_CHANGES_SINCE_GOLDEN", value: 6, threshold: 5,
      detail: "6 accepted baseline changes since golden v1 (more than 5)." }], steps_since_golden: 6, new_vs_current: {}, new_vs_golden: null,
      current_vs_golden: null, created_at: "2026-09-08T00:00:00Z" }] });
    go("#/drift");
    expect(await screen.findByText("Phase 8 review queue")).toBeTruthy();
    expect(await screen.findByText("Findings (1)")).toBeTruthy();
    fireEvent.click(await screen.findByText("Golden / poisoning (1)"));
    expect(await screen.findByText(/6 accepted baseline changes/)).toBeTruthy();
  });
});

describe("responsive-safe structure", () => {
  it.each(["#/advanced-drift", "#/baselines", "#/correlations", "#/replay/J1"])("%s wraps every table in a scroll container", async (hash) => {
    routes["/drift/findings"] = () => ({ items: [finding()], stats: { by_layer_status: {}, total: 1 } });
    routes["/drift/baselines"] = () => ({ total: 1, items: [current] });
    routes["/drift/correlations"] = () => ({ items: [correlation] });
    routes["/replay/jobs"] = () => ({ items: [job()] });
    routes["/replay/jobs/J1"] = () => job();
    const { container } = go(hash);
    await waitFor(() => expect(container.querySelectorAll("table").length).toBeGreaterThan(0));
    container.querySelectorAll("table").forEach((t) => expect(t.closest(".table-wrap")).not.toBeNull());
  });
});
