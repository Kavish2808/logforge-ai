// Demo Mode — one reproducible run of the real lifecycle. Progress comes from
// /demo/status (derived from stored rows); every step is performed by calling
// the normal public API from this page. Automatic steps run on "Run demo";
// every approval, drift review, activation and rollback waits for a click.
import { ReactNode, useEffect, useRef, useState } from "react";
import {
  acceptDrift, approveOnboarding, createOnboarding, getDemoFixtures, getDemoStatus, getEvent, getEvents, getSource,
  ingest, learningAction, listLearning, proposeLearning, resetDemo, suggestOnboarding,
} from "../api/endpoints";
import type { DemoFixtures, DemoReset, DemoStatus, DemoStepState } from "../api/types";
import { Badge, Card, Load, Stat } from "../components/ui";
import { num, providerLabel } from "../lib/format";
import { Link } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

type Log = { at: string; text: string; kind: "ok" | "fail" | "info" };

const STATE_LABEL: Record<DemoStepState, string> = {
  done: "completed", pending: "next (automatic)", human: "human decision", failed: "failed", waiting: "not started",
};

const DECISIONS: Record<string, { title: string; explain: string; button: string }> = {
  approve_onboarding: {
    title: "Approve the proposed adapter and activate v1",
    explain: "The offline analyzer's proposal passed the sandbox on the collected samples. Approving activates it as adapter v1 for this source; nothing parses these logs until you approve.",
    button: "Approve & activate v1",
  },
  accept_drift: {
    title: "Review the structural drift",
    explain: "A known source changed shape. Accepting it as a variant only updates the Phase 5 baseline — the adapter does not change.",
    button: "Accept drift as variant",
  },
  approve_learning: {
    title: "Approve the learned adapter v2",
    explain: "Phase 6 proposed a mapping change from accepted drift evidence and validated it against new and historical samples. Approval does not activate it.",
    button: "Approve v2 (do not activate)",
  },
  activate_learning: {
    title: "Activate adapter v2",
    explain: "v2 becomes the active adapter; v1 is kept (SUPERSEDED) for rollback and traceability.",
    button: "Activate v2",
  },
  rollback_learning: {
    title: "Roll back v2 to v1",
    explain: "Withdraws v2 and reactivates v1. No version, event or decision is deleted.",
    button: "Roll back to v1",
  },
};

function fixtureRaws(fx: DemoFixtures, kind: string) {
  return fx.fixtures.filter((f) => f.kind === kind);
}

export function Demo() {
  const fixtures = useApi((s) => getDemoFixtures(s), []);
  const status = useApi((s) => getDemoStatus(s), []);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Log[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [resetResult, setResetResult] = useState<DemoReset | null>(null);
  const [operator, setOperator] = useState("demo-operator");
  const [note, setNote] = useState("");
  const [evidenceTick, setEvidenceTick] = useState(0);
  const cancelled = useRef(false);
  // Reset on every mount: React StrictMode mounts, unmounts and remounts in development.
  useEffect(() => {
    cancelled.current = false;
    return () => { cancelled.current = true; };
  }, []);

  const say = (text: string, kind: Log["kind"] = "ok") =>
    setLog((l) => [...l, { at: new Date().toLocaleTimeString(), text, kind }]);

  /** Perform one automatic action with the public API. Ingest actions skip fixtures already ingested. */
  const auto = async (action: string, st: DemoStatus, fx: DemoFixtures) => {
    const once = async (key: string) => {
      if (st.events[key]) return;
      const f = fx.fixtures.find((x) => x.key === key)!;
      const ev = await ingest(f.raw);
      say(`POST /ingest ${key} → ${ev.status}${ev.adapter_id ? ` via ${ev.adapter_id}@v${ev.adapter_version}` : ""}`, ev.status === "SUCCESS" ? "ok" : "info");
    };
    switch (action) {
      case "ingest_probe": return once("unknown_probe");
      case "create_session": {
        const s = await createOnboarding({
          name: fx.namespace.session_name, samples: fixtureRaws(fx, "SAMPLE_V1").map((f) => f.raw),
          event_ids: st.events.unknown_probe ? [st.events.unknown_probe.event_id] : [],
        });
        return say(`POST /onboarding/sessions → ${s.status}, ${s.sample_count} samples`);
      }
      case "suggest_offline": {
        const s = await suggestOnboarding(st.onboarding_session!.id, "offline");
        return say(`POST /onboarding/sessions/…/suggest (offline) → ${s.status}, sandbox ${s.validation?.result ?? "—"}`);
      }
      case "ingest_v1_events":
        for (const f of fixtureRaws(fx, "EVENT_V1")) await once(f.key);
        return once("malformed");
      case "ingest_drift": return once("drift_trigger");
      case "propose_learning": {
        for (const f of fixtureRaws(fx, "EVIDENCE_V2")) await once(f.key);
        const L = await proposeLearning(st.events.drift_trigger.event_id, "offline", operator || null);
        return say(`POST /events/…/learning/propose → ${L.status}, risk ${L.risk}`);
      }
      case "ingest_checks":
        await once("check_v2");
        return once("check_v1");
      case "ingest_post_rollback": return once("post_rollback_v1");
      default: throw new Error(`No automatic action for ${action}`);
    }
  };

  /** Run automatic steps until the next human decision, a failure, or completion. */
  const runAuto = async () => {
    if (!fixtures.data) return;
    setBusy(true);
    setError(null);
    try {
      let lastStep: string | null = null;
      for (let i = 0; i < 20 && !cancelled.current; i++) {
        const st = await getDemoStatus();
        if (st.conflict) throw new Error(st.conflict);
        if (st.next.kind !== "pending" || !st.next.action) break;
        if (st.next.step === lastStep) throw new Error(`Step "${st.next.step}" did not complete after its action; see the failed step for details.`);
        lastStep = st.next.step;
        await auto(st.next.action, st, fixtures.data);
      }
    } catch (err) {
      setError(errorMessage(err));
      say(errorMessage(err), "fail");
    } finally {
      status.reload();
      setEvidenceTick((n) => n + 1);
      setBusy(false);
    }
  };

  /** Perform the human decision the user clicked, then continue with automatic steps. */
  const decide = async (action: string) => {
    const st = status.data!;
    const by = operator || null;
    setBusy(true);
    setError(null);
    try {
      if (action === "approve_onboarding") {
        const s = st.onboarding_session!;
        await approveOnboarding(s.id, { proposal_version: s.proposal_version, approved_by: by, note: note || null, adapter_id: st.namespace.source_key });
        say(`POST /onboarding/sessions/…/approve by ${by ?? "anonymous"} → adapter v1 ACTIVE`);
      } else if (action === "accept_drift") {
        await acceptDrift(st.events.drift_trigger.event_id, "add_variant", note || null);
        say("POST /events/…/drift/accept (add_variant) → baseline updated");
      } else if (action === "approve_learning") {
        const L = st.learning_session!;
        const r = await learningAction(L.id, "approve", { proposal_version: L.proposal_version, approved_by: by, note: note || null, confirm_supersede: false, activate: false });
        say(`POST /learning/sessions/…/approve → ${r.status}`);
      } else if (action === "activate_learning") {
        const r = await learningAction(st.learning_session!.id, "activate", { activated_by: by });
        say(`POST /learning/sessions/…/activate → ${r.status}`);
      } else if (action === "rollback_learning") {
        const r = await learningAction(st.learning_session!.id, "rollback", { reason: note || "demo rollback", requested_by: by });
        say(`POST /learning/sessions/…/rollback → ${r.status}`);
      }
      setNote("");
    } catch (err) {
      setError(errorMessage(err));
      say(errorMessage(err), "fail");
      setBusy(false);
      status.reload();
      return;
    }
    setBusy(false);
    await runAuto();
  };

  const doReset = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await resetDemo();
      setResetResult(r);
      setLog([]);
      say(`POST /demo/reset → removed ${Object.entries(r.deleted).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setConfirmReset(false);
      setBusy(false);
      status.reload();
      setEvidenceTick((n) => n + 1);
    }
  };

  const st = status.data;
  const started = !!st?.exists;
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Showcase · end-to-end</div>
          <h1>Demo Mode</h1>
          <p>One reproducible run of the real lifecycle: unknown vendor → adapter v1 → drift → learning → v2 → rollback. Every step calls the public API; every decision waits for you.</p>
        </div>
        <div className="row">
          <button className="primary" onClick={runAuto} disabled={busy || !st || st.complete || st.next.kind !== "pending" || !!st.conflict}>
            {busy ? "Working…" : st?.complete ? "Demo complete" : started ? "Continue demo" : "Run demo"}
          </button>
          {!confirmReset
            ? <button onClick={() => setConfirmReset(true)} disabled={busy || !st?.exists}>Reset demo</button>
            : <span className="boundary row" style={{ padding: "6px 10px" }}>
                <span className="small">Remove demo-owned data only?</span>
                <button className="danger" onClick={doReset} disabled={busy}>Confirm reset</button>
                <button className="ghost" onClick={() => setConfirmReset(false)}>Cancel</button>
              </span>}
        </div>
      </div>

      {st?.conflict && <div className="notice fail" role="alert">Demo namespace conflict: {st.conflict}</div>}
      {error && <div className="notice fail" role="alert">{error}</div>}
      {resetResult && (
        <div className="notice ok" role="status">
          Reset removed only demo-owned rows. Non-demo rows before/after:{" "}
          {Object.entries(resetResult.non_demo_rows.after).map(([k, v]) => `${k.replace(/_/g, " ")} ${resetResult.non_demo_rows.before[k]}→${v}`).join(" · ")}
          {resetResult.non_demo_rows.unchanged ? " (unchanged)" : ""}
        </div>
      )}

      <Load state={status}>
        {(s) => {
          const done = s.steps.filter((x) => x.state === "done").length;
          return (
            <Card className="hero" title="The LogForge story"
              actions={<span className="pill"><span className={`dot ${s.complete ? "ok" : ""}`} style={{ marginRight: 0 }} />{done} / {s.steps.length} steps complete</span>}>
              <p className="small" style={{ marginTop: -4, marginBottom: 12 }}>
                Unknown log → adaptive onboarding → parser created → new logs → drift detected → learning → new adapter version → rollback.
                Each tile below is a real lifecycle step and its state as recorded in the database.
              </p>
              <ol className="story" aria-label="Demo storyline">
                {s.steps.map((x) => (
                  <li key={x.key} className={x.state === "done" ? "done" : x.state === "pending" || x.state === "human" ? "now" : ""}>
                    <strong>{x.title}</strong>{STATE_LABEL[x.state]}
                  </li>
                ))}
              </ol>
            </Card>
          );
        }}
      </Load>

      <div className="grid g-main">
        <Card title="Lifecycle">
          <Load state={status}>
            {(s) => (
              <ol className="demo-steps" aria-label="Demo lifecycle">
                {s.steps.map((step) => (
                  <li key={step.key} className={`demo-step s-${step.state}`}>
                    <div className="demo-node" aria-hidden="true">{step.state === "done" ? "✓" : step.state === "failed" ? "✕" : step.state === "human" ? "!" : ""}</div>
                    <div className="demo-body">
                      <div className="spread">
                        <strong>{step.title}</strong>
                        <span className={`badge ${step.state === "done" ? "b-ok" : step.state === "failed" ? "b-fail" : step.state === "human" ? "b-review" : step.state === "pending" ? "b-warn" : ""}`}>
                          {STATE_LABEL[step.state]}
                        </span>
                      </div>
                      <StepDetail stepKey={step.key} s={s} />
                      {step.detail && <div className="small" style={{ color: "var(--fail)" }}>{step.detail}</div>}
                      {step.state === "human" && s.next.action && DECISIONS[s.next.action] && (
                        <div className="boundary" style={{ marginTop: 8 }}>
                          <div className="spread"><strong style={{ color: "var(--accent-2)" }}>HUMAN DECISION REQUIRED</strong><span className="small muted">Nothing happens until you click.</span></div>
                          <div style={{ margin: "6px 0" }}><strong>{DECISIONS[s.next.action].title}</strong></div>
                          <p className="small muted" style={{ margin: "0 0 8px" }}>{DECISIONS[s.next.action].explain}</p>
                          <DecisionContext action={s.next.action} s={s} />
                          <div className="row" style={{ marginTop: 8 }}>
                            <input aria-label="Decided by" value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="Decided by" />
                            <input aria-label="Decision note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" style={{ flex: 1, minWidth: 140 }} />
                            <button className={s.next.action === "rollback_learning" ? "danger" : "primary"} disabled={busy} onClick={() => decide(s.next.action!)}>
                              {DECISIONS[s.next.action].button}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Load>
        </Card>
        <div>
          <Card title="Demo namespace">
            <Load state={fixtures}>
              {(fx) => (
                <div className="small">
                  <div>Source key <code>{fx.namespace.source_key}</code></div>
                  <div>Session name <code>{fx.namespace.session_name}</code></div>
                  <div>Log marker <code>device={fx.namespace.marker}</code></div>
                  <div className="muted" style={{ marginTop: 6 }}>
                    {fx.fixtures.length} deterministic fixtures (fixed raw logs and SHA-256). Reset removes only rows it can prove are demo-owned.
                  </div>
                </div>
              )}
            </Load>
          </Card>
          <Evidence tick={evidenceTick} status={st ?? null} />
          <Card title="Activity (this page)">
            {log.length === 0 ? <div className="faint small">API calls made by this page appear here.</div> : (
              <ol className="small" style={{ margin: 0, paddingLeft: 18, maxHeight: 260, overflowY: "auto" }} aria-live="polite">
                {log.map((l, i) => <li key={i} style={{ color: l.kind === "fail" ? "var(--fail)" : undefined }}><span className="faint">{l.at}</span> {l.text}</li>)}
              </ol>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function EventLink({ s, k, label }: { s: DemoStatus; k: string; label?: string }) {
  const e = s.events[k];
  if (!e) return null;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
      <Link to="events" param={e.event_id}>{label ?? k.replace(/_/g, " ")}</Link>
      <Badge value={e.status} />{e.adapter_version && <span className="chip">v{e.adapter_version}</span>}
    </span>
  );
}

function StepDetail({ stepKey, s }: { stepKey: string; s: DemoStatus }) {
  const o = s.onboarding_session;
  const L = s.learning_session;
  const versions = s.adapter_versions.map((v) => `v${v.version} ${v.status}`).join(" · ");
  const count = (prefix: string) => Object.entries(s.events).filter(([k, e]) => k.startsWith(prefix) && e.status === "SUCCESS").length;
  const content: Record<string, ReactNode> = {
    unknown_source: s.events.unknown_probe ? <span>No adapter recognises the vendor: <EventLink s={s} k="unknown_probe" label="probe event" /> — raw log and SHA-256 preserved.</span> : null,
    samples_collected: o ? <span><Link to="onboarding" param={o.id}>Onboarding session</Link> with {o.sample_count} samples (12 pasted + the failed probe event).</span> : null,
    suggestion: o?.proposal_source ? <span>{providerLabel(o.proposal_source)}</span> : null,
    sandbox_v1: o?.validation_result ? <span>Sandbox {o.validation_result}{o.match_rate !== null ? ` · match rate ${Math.round(o.match_rate * 100)}%` : ""}</span> : null,
    approve_v1: o?.status === "APPROVED" ? <span>Approved — see the session decisions.</span> : null,
    adapter_v1_active: s.adapter_versions.length ? <span><Link to="evolution" param={s.namespace.source_key}>{s.adapter_versions.filter((v) => v.version === 1).map((v) => `v1 ${v.status}`).join("")}</Link></span> : null,
    events_normalized: count("event_v1_") ? <span>{count("event_v1_")} v1 logs SUCCESS · <EventLink s={s} k="malformed" label="malformed record" /></span> : null,
    drift_detected: s.events.drift_trigger ? <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}><EventLink s={s} k="drift_trigger" label="v2-structure log" /> {s.events.drift_trigger.drift_status && <Badge value={s.events.drift_trigger.drift_status} />}</span> : null,
    drift_accepted: s.events.drift_trigger?.drift_resolution ? <span>Human review: {s.events.drift_trigger.drift_resolution.replace(/_/g, " ")}</span> : null,
    learning_proposed: L ? <span><Link to="learning" param={L.id}>Learning session</Link> · risk <Badge value={L.risk} /> · {count("evidence_v2_")} v2 evidence logs</span> : null,
    learning_validated: L?.validation_result ? <span>Sandbox regression {L.validation_result}</span> : null,
    approve_v2: L && ["APPROVED", "ACTIVE", "ROLLED_BACK"].includes(L.status) ? <span>Learning session {L.status.replace(/_/g, " ")}</span> : null,
    adapter_v2_active: s.adapter_versions.some((v) => v.version === 2) ? <span>{versions}</span> : null,
    old_and_new_work: s.events.check_v2 || s.events.check_v1 ? <span className="row"><EventLink s={s} k="check_v2" label="new structure" /> <EventLink s={s} k="check_v1" label="old structure" /></span> : null,
    rollback_verified: s.events.post_rollback_v1 ? <span className="row">{versions} · <EventLink s={s} k="post_rollback_v1" label="v1 log after rollback" /></span> : null,
  };
  const c = content[stepKey];
  return c ? <div className="small muted" style={{ marginTop: 2 }}>{c}</div> : null;
}

function DecisionContext({ action, s }: { action: string; s: DemoStatus }) {
  const drift = useApi((sig) => (action === "accept_drift" && s.events.drift_trigger ? getEvent(s.events.drift_trigger.event_id, sig) : Promise.resolve(null)), [action, s.events.drift_trigger?.event_id]);
  if (action === "approve_onboarding" && s.onboarding_session) {
    return <div className="small">Review the mapping first: <Link to="onboarding" param={s.onboarding_session.id}>open the onboarding session</Link>.</div>;
  }
  if (action === "accept_drift") {
    const d = drift.data?.processing_metadata.drift;
    const diff = d?.differences ?? {};
    return d ? (
      <div className="small row">
        <span>Similarity {((d.similarity ?? 0) * 100).toFixed(1)}% (threshold {((d.threshold ?? 0) * 100).toFixed(0)}%) · severity <Badge value={d.severity ?? null} /></span>
        {(diff.added_fields ?? []).map((f) => <span key={f} className="chip add">+ {f}</span>)}
        {(diff.removed_fields ?? []).map((f) => <span key={f} className="chip rem">− {f}</span>)}
        <Link to="drift">Drift Queue</Link>
      </div>
    ) : null;
  }
  if ((action === "approve_learning" || action === "activate_learning") && s.learning_session) {
    return <div className="small">Risk <Badge value={s.learning_session.risk} /> · validation <Badge value={s.learning_session.validation_result} /> · <Link to="learning" param={s.learning_session.id}>review the proposal and its evidence</Link></div>;
  }
  if (action === "rollback_learning") {
    return <div className="small">Currently: {s.adapter_versions.map((v) => `v${v.version} ${v.status}`).join(" · ")} · <Link to="evolution" param={s.namespace.source_key}>Adapter Evolution</Link></div>;
  }
  return null;
}

/** Evidence counts from the existing read APIs (Views + Learning), never from this page's state. */
function Evidence({ tick, status }: { tick: number; status: DemoStatus | null }) {
  const key = status?.namespace.source_key;
  const marker = status?.namespace.marker;
  const data = useApi(async (sig) => {
    if (!key || !marker) return null;
    const count = async (q: Record<string, string>) => (await getEvents({ search: marker, limit: 1, include_total: true, ...q }, sig)).total ?? 0;
    const [total, success, partial, failed, underReview, drift] = await Promise.all([
      count({}), count({ status: "SUCCESS" }), count({ status: "PARTIAL" }), count({ status: "FAILED" }),
      count({ status: "UNDER_REVIEW" }), count({ drift_status: "DRIFT" }),
    ]);
    const [source, learning] = await Promise.all([
      // Under a namespace conflict the source key belongs to someone else: never show it as demo evidence.
      status?.conflict ? Promise.resolve(null) : getSource(key, sig).catch(() => null),
      status?.conflict ? Promise.resolve({ total: 0, items: [] }) : listLearning({ source_key: key, limit: 5 }, sig),
    ]);
    return { total, success, partial, failed, underReview, drift, source, learning: learning.items[0] ?? null };
  }, [tick, key, status?.conflict, status?.steps.map((x) => x.state).join()]);

  return (
    <Card title="Demo evidence">
      <Load state={data}>
        {(d) => d === null ? <div className="faint small">Loading…</div> : (
          <>
            <div className="stats" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))" }}>
              <Stat label="Events processed" value={num(d.total)} hint={`containing device=${marker}`} />
              <Stat label="Successful" value={num(d.success)} />
              <Stat label="Partial" value={num(d.partial)} />
              <Stat label="Failed" value={num(d.failed)} hint="raw preserved" />
              <Stat label="Under review" value={num(d.underReview)} />
              <Stat label="Drift detected" value={num(d.drift)} />
              <Stat label="Drift accepted" value={status?.events.drift_trigger?.drift_resolution ? "yes" : "no"} />
              <Stat label="Learning proposal" value={d.learning ? <Badge value={d.learning.status} /> : "—"} />
              <Stat label="Adapter version" value={d.source?.active_version ? `v${d.source.active_version}` : "—"} hint={d.source?.versions.map((v) => `v${v.version} ${v.status}`).join(" · ") || undefined} />
              <Stat label="Rollback" value={d.source?.versions.some((v) => v.status === "ROLLED_BACK") ? "verified" : "not yet"} />
            </div>
            <div className="row small" style={{ marginTop: 10 }}>
              <Link to="events">Event Explorer</Link>
              {status?.events.check_v2 && <Link to="events" param={status.events.check_v2.event_id}>Event Forensics</Link>}
              <Link to="drift">Drift Queue</Link>
              {key && <Link to="evolution" param={key}>Adapter Evolution</Link>}
              {d.learning ? <Link to="learning" param={d.learning.id}>Learning</Link> : <Link to="learning">Learning</Link>}
            </div>
          </>
        )}
      </Load>
    </Card>
  );
}
