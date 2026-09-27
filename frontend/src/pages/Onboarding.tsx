// Onboarding — Phase 3 unknown-vendor onboarding as a 7-step wizard over the
// existing /onboarding API. Suggestions are labelled by their real provider.
import { useState } from "react";
import {
  approveOnboarding, createOnboarding, getEvents, getOnboarding, listOnboarding, rejectOnboarding, suggestOnboarding,
} from "../api/endpoints";
import type { OnboardingSession } from "../api/types";
import { ActingAs, ConfidenceCard, SlaPanel } from "../components/trust";
import { Badge, Card, KV, Load, Pipeline, Stat } from "../components/ui";
import { fmtTime, providerLabel } from "../lib/format";
import { useAuth } from "../lib/auth";
import { Link, navigate } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

const MAX_SAMPLES = 50;
const STEPS = ["Samples", "Analyze", "Suggestion", "Mapping review", "Sandbox validation", "Human approval", "Activation"];

function stepFor(s: OnboardingSession): number {
  if (s.status === "APPROVED" || s.status === "REJECTED") return 6;
  if (s.validation?.result) return 5;
  if (s.status === "SUGGESTION_FAILED") return 2;
  return 2; // COLLECTED: samples analyzed, awaiting a suggestion
}

function NewSession({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unknown = useApi((s) => getEvents({ format: "unknown", status: "FAILED", limit: 50 }, s), []);
  const samples = text.split(/\r?\n/).filter((l) => l.trim());
  const total = samples.length + picked.length;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await createOnboarding({ name: name || null, samples, event_ids: picked });
      onCreated(s.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="New onboarding session">
      <div className="grid g2">
        <div>
          <label className="field">Source name (optional)
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. acme-firewall" maxLength={128} />
          </label>
          <label className="field" style={{ marginTop: 10 }}>Raw sample logs — one per line (10–15 recommended, max {MAX_SAMPLES})
            <textarea rows={10} className="mono" value={text} onChange={(e) => setText(e.target.value)}
              placeholder="Paste raw log lines from the unknown source" aria-label="Raw sample logs" />
          </label>
        </div>
        <div>
          <h3>Or use stored events that failed as unknown format</h3>
          <Load state={unknown} isEmpty={(d) => d.items.length === 0} empty="No FAILED unknown-format events stored.">
            {(d) => (
              <div style={{ maxHeight: 260, overflow: "auto" }}>
                {d.items.map((r) => (
                  <label key={r.event_id} className="row small" style={{ padding: "3px 0" }}>
                    <input type="checkbox" checked={picked.includes(r.event_id)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, r.event_id] : picked.filter((x) => x !== r.event_id))} />
                    <span className="mono">{r.event_id}</span><span className="faint">{fmtTime(r.received_at)}</span>
                  </label>
                ))}
              </div>
            )}
          </Load>
        </div>
      </div>
      <div className="spread" style={{ marginTop: 12 }}>
        <span className={`small ${total > MAX_SAMPLES ? "" : "muted"}`}>{total} sample(s) selected{total > MAX_SAMPLES ? ` — over the limit of ${MAX_SAMPLES}` : ""}</span>
        <button className="primary" onClick={submit} disabled={busy || total === 0 || total > MAX_SAMPLES}>Collect &amp; analyze</button>
      </div>
      {error && <div className="notice fail" role="alert" style={{ marginTop: 10 }}>{error}</div>}
    </Card>
  );
}

function List() {
  const sessions = useApi((s) => listOnboarding(s), []);
  const [creating, setCreating] = useState(false);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Onboarding</h1>
          <p>Teach LogForge an unknown vendor from samples: deterministic analysis, a suggested adapter, sandbox validation, and a human approval before anything is active.</p>
        </div>
        <button className="primary" onClick={() => setCreating(!creating)}>{creating ? "Close" : "New session"}</button>
      </div>
      <Pipeline steps={STEPS} />
      {creating && <div style={{ marginTop: 14 }}><NewSession onCreated={(id) => navigate("onboarding", id)} /></div>}
      <Card title="Sessions">
        <Load state={sessions} isEmpty={(d) => d.items.length === 0} empty="No onboarding sessions yet.">
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Created</th><th>Name</th><th>Samples</th><th>Proposal</th><th>Validation</th><th>Match rate</th><th>Adapter</th><th>Status</th></tr></thead>
                <tbody>
                  {d.items.map((s) => (
                    <tr key={s.id} className="clickable" tabIndex={0} onClick={() => navigate("onboarding", s.id)}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate("onboarding", s.id); }} aria-label={`Open onboarding session ${s.id}`}>
                      <td className="small">{fmtTime(s.created_at)}</td>
                      <td>{s.name ?? <span className="faint">—</span>}</td>
                      <td>{s.sample_count}</td>
                      <td className="mono">{s.proposal_version ? `v${s.proposal_version}` : "—"}</td>
                      <td><Badge value={s.validation_result} /></td>
                      <td>{s.match_rate === null ? "—" : `${(s.match_rate * 100).toFixed(0)}%`}</td>
                      <td className="mono">{s.adapter_id ? `${s.adapter_id} v${s.adapter_version}` : "—"}</td>
                      <td><Badge value={s.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </Card>
    </>
  );
}

function Detail({ id }: { id: string }) {
  const session = useApi((sig) => getOnboarding(id, sig), [id]);
  const [override, setOverride] = useState<OnboardingSession | null>(null);
  const [provider, setProvider] = useState("offline");
  const [by, setBy] = useState("");
  const { user } = useAuth();
  const who = user ? user.username : by;  // bound to the signed-in identity (Phase 7 RBAC)
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);

  const act = async (label: string, fn: () => Promise<OnboardingSession>) => {
    setBusy(true);
    setMsg(null);
    try {
      const s = await fn();
      setOverride(s);
      setMsg({ kind: "ok", text: `${label}: session is now ${s.status}.` });
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="onboarding">Onboarding</Link> / session</div>
          <h1 className="row">Onboarding session <code style={{ fontSize: 13 }}>{id}</code></h1>
        </div>
      </div>
      <Load state={session}>
        {(loaded) => {
          const s = override ?? loaded;
          const a = s.analysis ?? {};
          const v = s.validation;
          const m = v?.metrics;
          const open = s.status !== "APPROVED" && s.status !== "REJECTED";
          const step = stepFor(s);
          return (
            <>
              <Pipeline steps={STEPS} current={step} done={step} />

              <div className="grid g2" style={{ marginTop: 16 }}>
                <Card title="1 · Samples">
                  <p className="small muted">{s.sample_count} raw sample(s), stored verbatim with their SHA-256.</p>
                  <details>
                    <summary className="small">Show samples</summary>
                    <pre className="code">{s.samples.map((x) => x.raw).join("\n")}</pre>
                  </details>
                </Card>
                <Card title="2 · Deterministic analysis">
                  <KV items={[
                    ["Dominant format", String(a.dominant_format ?? "—")],
                    ["Parsed in that format", String(a.parsed_in_dominant_format ?? "—")],
                    ["Fields found", String(Object.keys(a.fields ?? {}).length)],
                    ["Optional fields", (a.optional_fields ?? []).join(", ") || null],
                    ["Structure variants", String(a.structure_variants ?? "—")],
                  ]} />
                </Card>
              </div>

              <Card title="3 · Adapter suggestion">
                <div className="row">
                  <span className="small muted">Current proposal:</span>
                  {s.proposal ? <strong>{providerLabel(s.proposal_source)}</strong> : <span className="faint">none yet</span>}
                  {s.proposal_version > 0 && <span className="chip">proposal v{s.proposal_version}</span>}
                </div>
                {s.suggestion_error && (
                  <div className="notice fail" style={{ marginTop: 10 }}>
                    Suggestion failed ({s.suggestion_error.kind}{s.suggestion_error.provider ? `, ${s.suggestion_error.provider}` : ""}): {s.suggestion_error.message}
                  </div>
                )}
                {open && (
                  <div className="row" style={{ marginTop: 10 }}>
                    <label className="field">Provider
                      <select value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="Suggestion provider">
                        <option value="offline">Offline analyzer (deterministic)</option>
                        <option value="auto">Auto — Claude if configured, else offline</option>
                        <option value="anthropic">Claude (requires a configured API key)</option>
                      </select>
                    </label>
                    <button onClick={() => act("Suggested", () => suggestOnboarding(s.id, provider))} disabled={busy}>
                      {s.proposal ? "Re-suggest" : "Suggest adapter"}
                    </button>
                  </div>
                )}
                <p className="small faint" style={{ marginTop: 8 }}>
                  The label above is the provider that actually produced the proposal, as recorded by the backend. Every proposal — from any provider — is validated in the sandbox below.
                </p>
              </Card>

              {s.proposal && (
                <Card title="4 · Mapping review">
                  <KV items={[["Vendor", s.proposal.vendor ?? null], ["Product", s.proposal.product ?? null], ["Format", s.proposal.format ?? null]]} />
                  {v?.accepted_mappings && (
                    <table style={{ marginTop: 10 }}>
                      <thead><tr><th>Raw field</th><th>Target</th><th>Confidence</th><th>Evidence</th><th>Result</th></tr></thead>
                      <tbody>
                        {v.accepted_mappings.map((x) => (
                          <tr key={`a${x.raw_field}`}><td className="mono">{x.raw_field}</td><td className="mono">{x.target}</td>
                            <td>{x.confidence.toFixed(2)}</td><td className="small">{x.evidence}</td><td><span className="badge b-ok">accepted</span></td></tr>
                        ))}
                        {(v.rejected_mappings ?? []).map((x) => (
                          <tr key={`r${x.raw_field}${x.target}`}><td className="mono">{x.raw_field}</td><td className="mono">{x.target}</td>
                            <td className="faint">—</td><td className="small">{x.reason}</td><td><span className="badge b-fail">rejected</span></td></tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <p className="small faint">Confidence is the proposer's own stated value; acceptance is decided by the sandbox checks, not by confidence.</p>
                </Card>
              )}

              {v && (
                <Card title={<span className="row">5 · Sandbox validation <Badge value={v.result ?? null} /></span>}>
                  {m && (
                    <div className="stats">
                      <Stat label="Match rate" value={`${(m.match_rate * 100).toFixed(0)}%`} hint={`${m.matched_samples}/${m.total_samples} samples${v.thresholds?.min_match_rate !== undefined ? ` · threshold ${(v.thresholds.min_match_rate * 100).toFixed(0)}%` : ""}`} />
                      <Stat label="Mapping coverage" value={`${(m.mapping_coverage * 100).toFixed(0)}%`} />
                      <Stat label="Structural consistency" value={`${(m.structural_consistency * 100).toFixed(0)}%`} />
                      <Stat label="Warnings" value={m.warning_count} hint={`${m.unknown_fields.length} unmapped field(s) preserved`} />
                    </div>
                  )}
                  <ul className="small" style={{ marginTop: 10, paddingLeft: 18 }}>{(v.reasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
                </Card>
              )}

              <Card title="6 · Human approval">
                {open ? (
                  <div className="boundary">
                    <div className="spread"><strong>Human decision required</strong><span className="small muted">No adapter is active until you approve.</span></div>
                    <ActingAs />
                    <div className="row" style={{ margin: "10px 0" }}>
                      <input aria-label="Reviewer" placeholder="Reviewer (free text)" value={who} disabled={!!user} title={user ? "Bound to your signed-in identity" : undefined} onChange={(e) => setBy(e.target.value)} />
                      <input aria-label="Note or reason" placeholder="Note / rejection reason" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
                    </div>
                    <div className="row">
                      <button className="primary" disabled={busy || !s.activation.eligible_for_approval}
                        onClick={() => act("Approved", async () => (await approveOnboarding(s.id, { proposal_version: s.proposal_version, approved_by: who || null, note: note || null })).session)}>
                        Approve &amp; activate proposal v{s.proposal_version}
                      </button>
                      <button className="danger" disabled={busy || !note.trim()}
                        onClick={() => act("Rejected", () => rejectOnboarding(s.id, { reason: note.trim(), rejected_by: who || null }))}>Reject</button>
                      {!s.activation.eligible_for_approval && <span className="small muted">Approval needs a proposal that PASSED validation.</span>}
                      {!note.trim() && <span className="small faint">Rejecting requires a reason.</span>}
                    </div>
                  </div>
                ) : (
                  <table>
                    <thead><tr><th>When</th><th>Action</th><th>By</th><th>Note</th></tr></thead>
                    <tbody>{s.decisions.map((x, i) => (
                      <tr key={i}><td className="small">{fmtTime(x.at as string)}</td><td><span className="chip">{String(x.action)}</span></td>
                        <td className="small">{String(x.by ?? "—")}</td><td className="small">{String(x.note ?? x.reason ?? "—")}</td></tr>
                    ))}</tbody>
                  </table>
                )}
                {msg && <div className={`notice ${msg.kind}`} role="status" style={{ marginTop: 10 }}>{msg.text}</div>}
              </Card>

              {open && s.status === "VALIDATED" && <SlaPanel itemType="ONBOARDING_SESSION" itemId={s.id} />}
              {s.proposal_version > 0 && <ConfidenceCard kind="onboarding" id={s.id} version={s.proposal_version} />}

              <Card title="7 · Activation">
                <div className="row">
                  <Badge value={s.activation.active ? "ACTIVE" : s.activation.state} />
                  {s.activation.adapter_id && (
                    <Link to="evolution" param={s.activation.adapter_id} className="mono">{s.activation.adapter_id} v{s.activation.adapter_version} →</Link>
                  )}
                </div>
              </Card>

              <Card title="Deterministic explanation"><pre className="code">{s.explanation}</pre></Card>
            </>
          );
        }}
      </Load>
    </>
  );
}

export function Onboarding({ sessionId }: { sessionId: string | null }) {
  return sessionId ? <Detail id={sessionId} /> : <List />;
}
