// Demo page: automatic steps stop at human boundaries, decisions call the real endpoints,
// evidence comes from the Views API. Fixtures live only in this test.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

type Call = { method: string; path: string; body: unknown; query: URLSearchParams };
let calls: Call[];
let routes: Record<string, (url: URL, body: unknown) => unknown>;

const STEPS = ["unknown_source", "samples_collected", "suggestion", "sandbox_v1", "approve_v1", "adapter_v1_active",
  "events_normalized", "drift_detected", "drift_accepted", "learning_proposed", "learning_validated", "approve_v2",
  "adapter_v2_active", "old_and_new_work", "rollback_verified"];

function status(doneCount: number, next: { step: string | null; kind: string; action: string | null }, extra: Record<string, unknown> = {}) {
  return {
    namespace: { session_name: "logforge-demo-firewall-v1", source_key: "logforge_demo_firewall", marker: "lfdemo-edge-fw01" },
    exists: doneCount > 0, conflict: null, complete: next.kind === "complete", next,
    steps: STEPS.map((key, i) => ({
      key, title: key, detail: null,
      state: i < doneCount ? "done" : key === next.step ? (next.kind === "human" ? "human" : "pending") : "waiting",
    })),
    onboarding_session: doneCount >= 2 ? { id: "01ONB", status: "VALIDATED", proposal_version: 1, proposal_source: "offline", validation_result: "PASSED", match_rate: 1, sample_count: 13 } : null,
    adapter_versions: [], learning_session: null,
    events: doneCount >= 1 ? { unknown_probe: { event_id: "01PROBE", status: "FAILED", adapter_id: null, adapter_version: null, drift_status: null, drift_resolution: null } } : {},
    ...extra,
  };
}

const fixtures = {
  namespace: { session_name: "logforge-demo-firewall-v1", source_key: "logforge_demo_firewall", marker: "lfdemo-edge-fw01", operator: "demo-operator" },
  fixtures: [
    { key: "unknown_probe", kind: "UNKNOWN_PROBE", raw: "probe-raw device=lfdemo-edge-fw01", sha256: "a", purpose: "" },
    ...Array.from({ length: 12 }, (_, i) => ({ key: `sample_v1_${i}`, kind: "SAMPLE_V1", raw: `sample ${i}`, sha256: `s${i}`, purpose: "" })),
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  calls = [];
  let phase = 0; // advances as the page performs real API calls
  routes = {
    "GET /health": () => ({ status: "ok" }),
    "GET /views/summary": () => ({ totals: {}, by_status: {}, trend: [] }),
    "GET /demo/fixtures": () => fixtures,
    "GET /demo/status": () => [
      status(0, { step: "unknown_source", kind: "pending", action: "ingest_probe" }),
      status(1, { step: "samples_collected", kind: "pending", action: "create_session" }),
      status(2, { step: "suggestion", kind: "pending", action: "suggest_offline" }),
      status(4, { step: "approve_v1", kind: "human", action: "approve_onboarding" }),
    ][phase],
    "POST /ingest": () => { phase = 1; return { event_id: "01PROBE", status: "FAILED", adapter_id: null }; },
    "POST /onboarding/sessions": () => { phase = 2; return { id: "01ONB", status: "COLLECTED", sample_count: 13 }; },
    "POST /onboarding/sessions/01ONB/suggest": () => { phase = 3; return { id: "01ONB", status: "VALIDATED", validation: { result: "PASSED" } }; },
    "POST /onboarding/sessions/01ONB/approve": () => ({ session: { status: "APPROVED" } }),
    "GET /views/events": (url) => ({ items: [], limit: 1, next_cursor: null, has_more: false, total: url.searchParams.get("status") === "FAILED" ? 1 : url.searchParams.get("status") ? 0 : 1 }),
    "GET /views/sources/logforge_demo_firewall": () => json({ error: { code: "NOT_FOUND", message: "no source" } }, 404),
    "GET /learning/sessions": () => ({ total: 0, items: [] }),
    "POST /demo/reset": () => ({ deleted: { events: 1 }, non_demo_rows: { before: { events: 11 }, after: { events: 11 }, unchanged: true } }),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body, query: url.searchParams });
    const handler = routes[`${method} ${path}`];
    if (!handler) return json({ error: { code: "NOT_FOUND", message: `no mock for ${method} ${path}` } }, 404);
    const result = handler(url, body);
    return result instanceof Response ? result : json(result);
  }));
  window.location.hash = "#/demo";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("demo mode", () => {
  it("runs automatic steps through the real APIs and stops at the first human decision", async () => {
    // StrictMode (as in main.tsx) mounts, unmounts and remounts: Run must still work afterwards.
    render(<StrictMode><App /></StrictMode>);
    const run = await screen.findByRole("button", { name: "Run demo" });
    await waitFor(() => expect((run as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(run);
    expect(await screen.findByText("HUMAN DECISION REQUIRED")).toBeTruthy();
    const posts = calls.filter((c) => c.method === "POST").map((c) => c.path);
    expect(posts).toEqual(["/ingest", "/onboarding/sessions", "/onboarding/sessions/01ONB/suggest"]);
    expect(calls.find((c) => c.path === "/onboarding/sessions")!.body).toMatchObject({
      name: "logforge-demo-firewall-v1", event_ids: ["01PROBE"],
    });
    expect((calls.find((c) => c.path === "/onboarding/sessions")!.body as { samples: string[] }).samples).toHaveLength(12);
    expect(calls.find((c) => c.path.endsWith("/suggest"))!.body).toEqual({ provider: "offline" });
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false); // never auto-approved
  });

  it("calls the approval endpoint only when the human clicks, with the demo adapter id", async () => {
    routes["GET /demo/status"] = () => status(4, { step: "approve_v1", kind: "human", action: "approve_onboarding" });
    render(<App />);
    const approve = await screen.findByRole("button", { name: "Approve & activate v1" });
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
    fireEvent.change(screen.getByLabelText("Decided by"), { target: { value: "analyst-7" } });
    fireEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.path === "/onboarding/sessions/01ONB/approve")).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/approve"))!.body).toEqual({
      proposal_version: 1, approved_by: "analyst-7", note: null, adapter_id: "logforge_demo_firewall",
    });
  });

  it("shows evidence counts from the Views API, not page state", async () => {
    routes["GET /demo/status"] = () => status(1, { step: "samples_collected", kind: "pending", action: "create_session" });
    render(<App />);
    const label = await screen.findByText("Events processed");
    await waitFor(() => expect(label.parentElement!.textContent).toContain("1"));
    const evidenceCalls = calls.filter((c) => c.path === "/views/events");
    expect(evidenceCalls.every((c) => c.query.get("search") === "lfdemo-edge-fw01" && c.query.get("include_total") === "true")).toBe(true);
    expect(screen.getByText("Failed").parentElement!.textContent).toContain("1");
  });

  it("reset requires confirmation and reports that non-demo rows are unchanged", async () => {
    routes["GET /demo/status"] = () => status(1, { step: "samples_collected", kind: "pending", action: "create_session" });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Reset demo" }));
    expect(calls.some((c) => c.path === "/demo/reset")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Confirm reset" }));
    expect(await screen.findByText(/Reset removed only demo-owned rows/)).toBeTruthy();
    expect(screen.getByText(/events 11→11/)).toBeTruthy();
  });

  it("shows a namespace conflict and disables Run", async () => {
    routes["GET /demo/status"] = () => ({ ...status(0, { step: "unknown_source", kind: "pending", action: "ingest_probe" }), conflict: "adapter 'logforge_demo_firewall' has version(s) [1] that were not created by the demo" });
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("not created by the demo");
    expect((screen.getByRole("button", { name: "Run demo" }) as HTMLButtonElement).disabled).toBe(true);
    // the foreign adapter holding the demo key is never presented as demo evidence
    await screen.findByText("Events processed");
    expect(calls.some((c) => c.path.startsWith("/views/sources/") || c.path === "/learning/sessions")).toBe(false);
  });
});
