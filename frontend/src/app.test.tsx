// Console tests with a mocked fetch. Fixtures live only here — the app itself renders API data only.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

type Handler = (url: URL, init?: RequestInit) => unknown;
let routes: Record<string, Handler>;
let calls: { url: URL; init?: RequestInit }[];

const row = (over: Record<string, unknown> = {}) => ({
  event_id: "01EVT0000000000000000000A1", received_at: "2026-09-20T10:00:00Z", event_timestamp: null, status: "SUCCESS",
  format_detected: "cef", vendor: "Fortinet", product: "FortiGate", source_key: "fortinet_fortigate", adapter_id: "fortinet_fortigate",
  adapter_version: "1", adapter_source: "shipped", ocsf_class_name: "Network Activity", ocsf_category_name: "Network", event_type: null,
  event_action: "allow", severity: "low", drift_status: "NORMAL", drift_severity: null, warning_count: 0, preserved_field_count: 1,
  raw_hash: "a".repeat(64), ...over,
});
const page = (items: unknown[], over: Record<string, unknown> = {}) => ({ items, limit: 50, next_cursor: null, has_more: false, total: items.length, ...over });

const summary = {
  window: {}, totals: { events: 7, unique_sources: 2, unique_vendors: 2, drift_events: 1, pending_reviews: 1 },
  by_status: { SUCCESS: 4, PARTIAL: 1, FAILED: 1, UNDER_REVIEW: 1 }, by_format: { cef: 7 }, by_vendor: {}, by_adapter: {},
  by_drift_status: { NORMAL: 5, DRIFT: 1 }, by_drift_severity: {}, adapters: { shipped_vendor: 3 }, onboarding_sessions: {},
  learning_sessions: {}, trend: [],
};
const filters = { statuses: ["SUCCESS", "FAILED"], formats: ["cef", "unknown"], vendors: ["Fortinet"], products: [], sources: ["fortinet_fortigate"],
  adapters: [], categories: [], severities: [], drift_statuses: ["NORMAL", "DRIFT"], drift_severities: [] };
const source = {
  source_key: "fortinet_fortigate", kind: "shipped_vendor", vendor: "Fortinet", product: "FortiGate", events: { total: 5, SUCCESS: 4, PARTIAL: 1 },
  partial_rate: 0.2, formats: { cef: 5 }, adapter_versions_seen: { "1": 5 }, active_version: null,
  baseline: { version: 2, origin: "auto", reference_field_count: 9, accepted_variants: 1, adapter_version: "1", updated_at: "2026-09-20T10:00:00Z" },
  drift: { DRIFT: 1 }, under_review: 1, learning_sessions: {}, last_seen: "2026-09-20T10:00:00Z",
  versions: [], baseline_history: [{ version: 2, action: "ACCEPT_VARIANT", changes: { added_fields: ["newfield"] }, created_at: "2026-09-20T10:00:00Z" }],
  recent_drift: [row({ event_id: "01DRIFT", drift_status: "DRIFT", status: "UNDER_REVIEW" })],
};
const lineage = {
  event_id: "01EVT0000000000000000000A1", status: "PARTIAL",
  chain: [
    { stage: "RAW", outcome: "OK", summary: "Raw event stored verbatim", details: { received_at: "2026-09-20T10:00:00Z" } },
    { stage: "NORMALIZATION", outcome: "WARN", summary: "Normalized with 1 warning", details: {} },
    { stage: "WARNINGS", outcome: "WARN", summary: "1 warning", details: { warnings: [{ kind: "TYPE_MISMATCH_PRESERVED", message: "dstport: value 'abc' is not an integer" }] } },
  ],
  integrity: { algorithm: "sha256", stored: "a".repeat(64), recomputed: "a".repeat(64), verified: true, raw_bytes: 120 },
  field_accounting: {
    parsed_count: 3, mapped_count: 2, preserved_count: 1, unaccounted: [], extensions_not_in_parse: [],
    fields: [
      { field: "src", outcome: "MAPPED", target: "network.src_ip", normalized_value_present: true },
      { field: "dstport", outcome: "PRESERVED", location: "extensions.dstport", value: "abc" },
    ],
  },
  nothing_silently_discarded: true, basis: ["every parsed field is mapped or preserved", "raw hash verified"],
};
const onboarding = {
  id: "01ONB", name: "acme", status: "VALIDATED", sample_count: 12, proposal_version: 1, validation_result: "PASSED", match_rate: 1,
  adapter_id: null, adapter_version: null, created_at: "2026-09-20T10:00:00Z", samples: [{ index: 0, raw: "a=1 b=2 c=3", raw_hash: "x", source_event_id: null }],
  analysis: { dominant_format: "kv", fields: { a: {}, b: {}, c: {} } }, proposal: { vendor: "Acme", product: "FW", format: "kv" },
  proposal_source: "offline", suggestion_error: null,
  validation: { result: "PASSED", reasons: ["match rate 100%"], accepted_mappings: [{ raw_field: "a", target: "network.src_ip", confidence: 0.9, evidence: "ipv4 values" }],
    rejected_mappings: [], metrics: { total_samples: 12, matched_samples: 12, failed_samples: 0, match_rate: 1, mapping_coverage: 0.66, unknown_fields: ["c"], warning_count: 0, structural_consistency: 1 },
    thresholds: { min_match_rate: 0.9 } },
  decisions: [], activation: { active: false, state: "NOT_ACTIVE_AWAITING_APPROVAL", eligible_for_approval: true, adapter_id: null, adapter_version: null },
  explanation: "deterministic explanation",
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
  routes = {
    "/health": () => ({ status: "ok" }),
    "/views/summary": () => summary,
    "/views/filters": () => filters,
    "/views/events": () => page([row()]),
    "/views/sources": () => ({ total: 1, items: [source] }),
    "/views/sources/fortinet_fortigate": () => source,
    "/views/sources/fortinet_fortigate/timeline": () => ({ source_key: "fortinet_fortigate", entries: [] }),
    "/views/events/01EVT0000000000000000000A1/lineage": () => lineage,
    "/events/01EVT0000000000000000000A1": () => new Response(JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "legacy row" } }), { status: 500 }),
    "/learning/sessions": () => ({ total: 0, items: [] }),
    "/onboarding/sessions": () => ({ total: 1, items: [onboarding] }),
    "/onboarding/sessions/01ONB": () => onboarding,
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("routes render", () => {
  it.each([
    ["#/overview", "Overview"], ["#/events", "Event Explorer"], ["#/sources", "Sources"], ["#/drift", "Drift Queue"],
    ["#/onboarding", "Onboarding"], ["#/evolution", "Adapter Evolution"], ["#/learning", "Learning"], ["#/export", "Export"], ["#/demo", "Demo Mode"],
  ])("%s", async (hash, heading) => {
    go(hash);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeTruthy();
  });
});

describe("loading and error states", () => {
  it("shows loading, then an error with a working retry", async () => {
    let fail = true;
    routes["/views/sources"] = () => (fail ? new Response(JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "database down" } }), { status: 500 }) : { total: 1, items: [source] });
    go("#/sources");
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("database down");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("fortinet_fortigate")).toBeTruthy();
  });

  it("renders overview numbers only from the summary response", async () => {
    go("#/overview");
    expect(await screen.findByText("Total events")).toBeTruthy();
    const stat = screen.getByText("Total events").parentElement!;
    expect(stat.textContent).toContain("7");
  });
});

describe("event explorer", () => {
  it("sends selected filters to /views/events and can reset them", async () => {
    go("#/events");
    const status = (await screen.findByRole("combobox", { name: "Status" })) as HTMLSelectElement;
    await waitFor(() => expect(status.querySelectorAll("option").length).toBeGreaterThan(1));
    fireEvent.change(status, { target: { value: "FAILED" } });
    await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/views/events") && c.url.searchParams.get("status") === "FAILED")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /reset filters/i }));
    await waitFor(() => expect(status.value).toBe(""));
  });

  it("debounces search and ignores terms shorter than 3 characters", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      go("#/events");
      const search = await screen.findByRole("textbox", { name: "Search" });
      fireEvent.change(search, { target: { value: "ab" } });
      await act(async () => { vi.advanceTimersByTime(500); });
      expect(calls.some((c) => c.url.searchParams.has("search"))).toBe(false);
      fireEvent.change(search, { target: { value: "abc" } });
      await act(async () => { vi.advanceTimersByTime(500); });
      await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("search") === "abc")).toBe(true));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("event forensics", () => {
  it("renders the verdict, lineage and preserved type-mismatch field without claiming conversion", async () => {
    go("#/events/01EVT0000000000000000000A1");
    expect(await screen.findByText("NOTHING SILENTLY DISCARDED")).toBeTruthy();
    expect(screen.getByLabelText("Processing lineage").querySelectorAll("li").length).toBe(3);
    // identifiers are rendered with <wbr> break points, so match on the element's full text
    expect(screen.getAllByText((_, el) => el?.tagName === "DIV" && el.textContent === "extensions.dstport").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("tab", { name: /warnings/i }));
    const notice = await screen.findByText(/TYPE_MISMATCH: not converted, not mapped/);
    expect(notice.textContent).not.toMatch(/\bconverted to\b/i);
    // /events/{id} failed (legacy row) — the page degrades instead of crashing.
    fireEvent.click(screen.getByRole("tab", { name: /raw event/i }));
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
  });
});

describe("drift queue", () => {
  it("lists pending drift and requires an explicit confirmed decision", async () => {
    routes["/views/events"] = (url) => page(url.searchParams.get("status") === "UNDER_REVIEW"
      ? [row({ event_id: "01DRIFT", status: "UNDER_REVIEW", drift_status: "DRIFT", drift_severity: "MEDIUM" })] : []);
    routes["/events/01DRIFT"] = () => ({
      event_id: "01DRIFT", status: "UNDER_REVIEW", raw_event: "x", extensions: {}, warnings: [],
      structural_fingerprint: { field_order: ["a", "newfield"] },
      processing_metadata: { drift: { status: "DRIFT", source_key: "fortinet_fortigate", similarity: 0.7, threshold: 0.85,
        differences: { added_fields: ["newfield"] }, change_types: ["FIELD_ADDED"], recommended_actions: ["REVIEW"] } },
    });
    routes["/drift/baselines/fortinet_fortigate"] = () => ({ source_key: "fortinet_fortigate", version: 2, origin: "auto", fingerprint: { field_order: ["a"] }, accepted_variants: [] });
    let accepted: unknown = null;
    routes["POST /events/01DRIFT/drift/accept"] = (_u, init) => { accepted = JSON.parse(String(init!.body)); return { event: {} }; };
    go("#/drift");
    fireEvent.click(await screen.findByRole("row", { name: /review drift 01DRIFT/i }));
    expect(await screen.findByText("+ newfield")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept as variant" }));
    expect(accepted).toBeNull(); // choosing an action alone changes nothing
    fireEvent.click(screen.getByRole("button", { name: /confirm: add variant/i }));
    await waitFor(() => expect(accepted).toEqual({ mode: "add_variant", note: null }));
  });
});

describe("source detail", () => {
  it("shows the baseline, history and recent drift from the API", async () => {
    go("#/sources/fortinet_fortigate");
    expect(await screen.findByText("Baseline (Phase 5)")).toBeTruthy();
    expect(screen.getByText("+newfield")).toBeTruthy();
    expect(screen.getByText("20.0%")).toBeTruthy();
    expect(screen.getByText(/versioned in the repository/)).toBeTruthy();
  });
});

describe("onboarding", () => {
  it("labels an offline suggestion honestly and never as Claude", async () => {
    go("#/onboarding/01ONB");
    const label = await screen.findByText("Offline analyzer (deterministic)", { selector: "strong" });
    expect(label.textContent).not.toMatch(/claude/i);
    expect(screen.getByRole("button", { name: /approve & activate proposal v1/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" }).hasAttribute("disabled")).toBe(true); // reason required
  });
});

describe("export", () => {
  it("shows the real matching count and a disabled download", async () => {
    routes["/views/events"] = () => page([row()], { total: 42 });
    go("#/export");
    expect(await screen.findByText("42")).toBeTruthy();
    expect(screen.getByText(/Export is not available yet/)).toBeTruthy();
    const button = screen.getByRole("button", { name: /download/i });
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});
