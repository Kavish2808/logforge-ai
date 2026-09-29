// Learning — Phase 6 controlled adaptation. Every action calls the existing
// learning API; approval and activation are separate, explicit steps.
import { useState } from "react";
import { getLearning, learningAction, listLearning } from "../api/endpoints";
import type { LearningSession } from "../api/types";
import { ActingAs, ConfidenceCard, SlaPanel } from "../components/trust";
import { Badge, Card, KV, Load, Pipeline, Stat } from "../components/ui";
import { fmtTime, providerLabel } from "../lib/format";
import { useAuth } from "../lib/auth";
import { Link, navigate } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";
import { ShadowPanel } from "./ShadowValidation";

const STATUSES = ["PROPOSED", "VALIDATED", "NEEDS_REVIEW", "FAILED", "APPROVED", "ACTIVE", "ROLLED_BACK", "REJECTED", "NO_CHANGE_REQUIRED"];
const FLOW = ["Drift", "Evidence", "Proposal", "Validation", "Human approval", "Adapter version"];

function flowPosition(status: string): number {
  if (status === "ACTIVE" || status === "ROLLED_BACK") return 6;
  if (status === "APPROVED") return 5;
  if (["VALIDATED", "NEEDS_REVIEW", "FAILED", "NO_CHANGE_REQUIRED", "REJECTED"].includes(status)) return 4;
  return 3;
}

function List() {
  const [status, setStatus] = useState("");
  const sessions = useApi((s) => listLearning({ status, limit: 100 }, s), [status]);
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Adapt · continuous learning</div>
          <h1>Learning</h1>
          <p>Phase 6 learns only drift a human accepted — through validation and a separate approval. Nothing activates on its own.</p>
        </div>
        <label className="field">Status
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="">All</option>{STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </div>
      <Pipeline steps={FLOW} />
      <Card title="Learning sessions" className="">
        <Load state={sessions} isEmpty={(d) => d.items.length === 0}
          empty="No learning sessions. Accept a drift in the Drift Queue, then choose “Propose learning”.">
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Created</th><th>Source</th><th>Version</th><th>Modes</th><th>Risk</th><th>Validation</th><th>Status</th></tr></thead>
                <tbody>
                  {d.items.map((l) => (
                    <tr key={l.id} className="clickable" tabIndex={0} onClick={() => navigate("learning", l.id)}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate("learning", l.id); }} aria-label={`Open learning session ${l.id}`}>
                      <td className="small">{fmtTime(l.created_at)}</td>
                      <td className="mono">{l.source_key}</td>
                      <td className="mono">v{l.source_adapter_version}{l.target_version ? ` → v${l.target_version}` : ""}</td>
                      <td className="small">{l.learning_modes.join(", ") || "—"}</td>
                      <td><Badge value={l.risk} /></td>
                      <td><Badge value={l.validation_result} /></td>
                      <td><Badge value={l.status} /></td>
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

function Actions({ s, onDone }: { s: LearningSession; onDone: (updated: LearningSession) => void }) {
  const [by, setBy] = useState("");
  const { user } = useAuth();
  const who = user ? user.username : by;  // bound to the signed-in identity (Phase 7 RBAC)
  const [note, setNote] = useState("");
  const [confirmSupersede, setConfirmSupersede] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const compat = !!s.validation?.compatibility_confirmation_required;
  const canApprove = s.status === "VALIDATED" || (s.status === "NEEDS_REVIEW" && compat);

  const run = async (action: string, body: object, label: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const updated = await learningAction(s.id, action, body);
      setMsg({ kind: "ok", text: `${label}: session is now ${updated.status}.` });
      onDone(updated);
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  const approveBody = (activate: boolean) => ({
    proposal_version: s.proposal_version, approved_by: who || null, note: note || null, confirm_supersede: confirmSupersede, activate,
  });
  const reason = note || "no reason given";

  return (
    <div className="boundary">
      <div className="spread"><strong>Human control</strong><span className="small muted">State: <Badge value={s.status} /></span></div>
      <div className="row" style={{ margin: "10px 0" }}>
        <input aria-label="Reviewer" placeholder="Reviewer (free text)" value={who} disabled={!!user} title={user ? "Bound to your signed-in identity" : undefined} onChange={(e) => setBy(e.target.value)} />
        <input aria-label="Note or reason" placeholder="Note / reason" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
      </div>
      {compat && canApprove && (
        <label className="row small" style={{ marginBottom: 10 }}>
          <input type="checkbox" checked={confirmSupersede} onChange={(e) => setConfirmSupersede(e.target.checked)} />
          I confirm the new version intentionally supersedes behavior for historical logs (compatibility below threshold).
        </label>
      )}
      <div className="row">
        {["PROPOSED", "VALIDATED", "NEEDS_REVIEW", "FAILED", "NO_CHANGE_REQUIRED"].includes(s.status) &&
          <button onClick={() => run("validate", {}, "Re-validated")} disabled={busy}>Re-validate</button>}
        {canApprove && <button onClick={() => run("approve", approveBody(false), "Approved")} disabled={busy || (compat && !confirmSupersede)}>Approve</button>}
        {canApprove && <button className="primary" onClick={() => run("approve", approveBody(true), "Approved & activated")} disabled={busy || (compat && !confirmSupersede)}>Approve &amp; activate</button>}
        {s.status === "APPROVED" && <button className="primary" onClick={() => run("activate", { activated_by: who || null }, "Activated")} disabled={busy}>Activate v{s.target_version}</button>}
        {["PROPOSED", "VALIDATED"].includes(s.status) && <button onClick={() => run("request-review", { reason, by: who || null }, "Review requested")} disabled={busy}>Request review</button>}
        {["PROPOSED", "VALIDATED", "NEEDS_REVIEW", "FAILED", "APPROVED", "NO_CHANGE_REQUIRED"].includes(s.status) &&
          <button className="danger" onClick={() => run("reject", { reason, by: who || null }, "Rejected")} disabled={busy}>Reject</button>}
        {s.status === "ACTIVE" && <button className="danger" onClick={() => run("rollback", { reason: note || null, requested_by: who || null }, "Rolled back")} disabled={busy}>Roll back v{s.target_version}</button>}
        {["REJECTED", "ROLLED_BACK"].includes(s.status) && <span className="small muted">Terminal state — no further actions.</span>}
      </div>
      {msg && <div className={`notice ${msg.kind}`} style={{ marginTop: 10 }} role="status">{msg.text}</div>}
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const session = useApi((sig) => getLearning(id, sig), [id]);
  const [override, setOverride] = useState<LearningSession | null>(null);
  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="learning">Learning</Link> / session</div>
          <h1 className="row">Learning session <code style={{ fontSize: 13 }}>{id}</code></h1>
        </div>
      </div>
      <Load state={session}>
        {(loaded) => {
          const s = override ?? loaded;
          const v = s.validation ?? {};
          const d = s.proposal ?? {};
          const diff = s.mapping_diff;
          return (
            <>
              <Pipeline steps={FLOW} done={flowPosition(s.status)} current={Math.min(flowPosition(s.status), FLOW.length - 1)} />
              <div className="stats" style={{ marginTop: 14 }}>
                <Stat accent label="Source" value={<Link to="evolution" param={s.source_key} className="mono">{s.source_key}</Link>} />
                <Stat label="Version" value={`v${s.source_adapter_version}${s.target_version ? ` → v${s.target_version}` : ""}`} hint={s.target_version_status ?? "not created"} />
                <Stat label="Status" value={<Badge value={s.status} />} />
                <Stat label="Risk" value={<Badge value={s.risk} />} hint={s.risk_reasons[0]} />
                <Stat label="Recommendation" value={<span style={{ fontSize: 13 }}>{s.recommendation}</span>} />
                <Stat label="Proposal source" value={<span style={{ fontSize: 13 }}>{providerLabel(s.proposal_source)}</span>} hint={`proposal v${s.proposal_version}`} />
              </div>
              {s.assistant?.error && (
                <div className="notice warn" style={{ marginTop: 12 }}>
                  LLM assistant not used ({s.assistant.error.kind}): {s.assistant.error.message} The deterministic engine produced this proposal.
                </div>
              )}
              <div className="grid g2" style={{ marginTop: 16 }}>
                <Card title="Drift & evidence">
                  <KV items={[
                    ["Trigger event", <Link key="t" to="events" param={s.trigger_event_id} className="mono">{s.trigger_event_id}</Link>],
                    ["Change types", (s.drift.change_types ?? []).join(", ") || null],
                    ["Drift severity", <Badge key="sev" value={s.drift.severity} />],
                    ["Similarity", typeof s.drift.similarity === "number" ? `${(s.drift.similarity * 100).toFixed(1)}% (threshold ${((s.drift.threshold ?? 0) * 100).toFixed(0)}%)` : null],
                    ["Human review", s.drift.review?.resolution ?? null],
                    ["Drifted evidence", `${s.evidence.drifted.length} stored event(s)`],
                    ["Historical evidence", `${s.evidence.historical.length} stored event(s)`],
                  ]} />
                </Card>
                <Card title="What the new version changes">
                  {diff ? (
                    <>
                      <div className="row">
                        {diff.added.map((x) => <span key={x} className="chip add">+ {x}</span>)}
                        {diff.removed.map((x) => <span key={x} className="chip rem">− {x}</span>)}
                        {diff.changed.map((x) => <span key={x} className="chip chg">~ {x}</span>)}
                        {diff.optional_fields.map((x) => <span key={x} className="chip">? {x} now optional</span>)}
                      </div>
                      <p className="small muted">{diff.unchanged.length} existing mapping(s) unchanged.</p>
                    </>
                  ) : <div className="faint">No new version is proposed ({s.status === "NO_CHANGE_REQUIRED" ? "no mapping change needed" : "proposal not valid"}).</div>}
                  {(d.unresolved ?? []).length > 0 && (
                    <>
                      <h3 style={{ marginTop: 10 }}>Unresolved</h3>
                      <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{d.unresolved!.map((u) => <li key={u.field}><span className="mono">{u.field}</span> <span className="badge">{u.kind}</span> {u.reason}</li>)}</ul>
                    </>
                  )}
                </Card>
              </div>
              {((d.add_mappings ?? []).length > 0 || (d.remaps ?? []).length > 0) && (
                <Card title="Proposed mappings and their evidence">
                  <table>
                    <thead><tr><th>Field</th><th>Target</th><th>Confidence</th><th>Evidence</th></tr></thead>
                    <tbody>
                      {(d.add_mappings ?? []).map((m) => (
                        <tr key={m.raw_field}><td className="mono">+ {m.raw_field}</td><td className="mono">{m.target}</td>
                          <td><Badge value={m.confidence} /></td><td className="small"><ul style={{ margin: 0, paddingLeft: 16 }}>{m.evidence.map((e) => <li key={e}>{e}</li>)}</ul></td></tr>
                      ))}
                      {(d.remaps ?? []).map((m) => (
                        <tr key={m.to_field}><td className="mono">{m.from_field} ⇄ {m.to_field}</td><td className="mono">{m.target}</td>
                          <td><Badge value={m.confidence} /></td><td className="small"><ul style={{ margin: 0, paddingLeft: 16 }}>{m.evidence.map((e) => <li key={e}>{e}</li>)}</ul></td></tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="small faint">Confidence grades the evidence behind a mapping (HIGH/MEDIUM: deterministic engine; LOW: assistant suggestion). Sandbox results are separate.</p>
                </Card>
              )}
              <Card title={<span className="row">Sandbox validation <Badge value={v.result ?? null} /></span>}>
                <div className="stats">
                  <Stat label="New structure" value={v.new_structure ? `${v.new_structure.matched_samples}/${v.new_structure.total_samples}` : "—"} hint="drifted samples parsed" />
                  <Stat label="Mapping coverage" value={v.new_structure ? `${(v.new_structure.mapping_coverage * 100).toFixed(0)}%` : "—"} />
                  <Stat label="Historical samples" value={v.historical ? v.historical.total_samples : "—"} hint="must normalize identically" />
                  <Stat label="Regressions" value={(v.regressions ?? []).length} hint="historical samples that would change" />
                </div>
                {v.compatibility_confirmation_required && <div className="notice warn" style={{ marginTop: 10 }}>Backward compatibility is below threshold — approval requires explicit supersede confirmation.</div>}
                <ul className="small" style={{ marginTop: 10, paddingLeft: 18 }}>{(v.reasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
              </Card>
              {(s.candidate || ["VALIDATED", "NEEDS_REVIEW", "APPROVED", "ACTIVE"].includes(s.status)) && <ShadowPanel session={s} />}
              <Card title="Decision">
                <ActingAs />
                {["PROPOSED", "VALIDATED", "NEEDS_REVIEW", "FAILED", "APPROVED"].includes(s.status) && <SlaPanel itemType="LEARNING_SESSION" itemId={s.id} />}
                <Actions s={s} onDone={(u) => { setOverride(u); session.reload(); }} />
              </Card>
              {s.proposal_version > 0 && <ConfidenceCard kind="learning" id={s.id} version={s.proposal_version} />}
              <div className="grid g2" style={{ marginTop: 16 }}>
                <Card title="History">
                  <table>
                    <thead><tr><th>When</th><th>Action</th><th>By</th><th>Note</th></tr></thead>
                    <tbody>{s.decisions.map((x, i) => <tr key={i}><td className="small">{fmtTime(x.at)}</td><td><span className="chip">{x.action}</span></td><td className="small">{x.by ?? "—"}</td><td className="small">{x.note ?? "—"}</td></tr>)}</tbody>
                  </table>
                </Card>
                <Card title="Deterministic learning report"><pre className="code">{s.report}</pre></Card>
              </div>
            </>
          );
        }}
      </Load>
    </>
  );
}

export function Learning({ sessionId }: { sessionId: string | null }) {
  return sessionId ? <Detail id={sessionId} /> : <List />;
}
