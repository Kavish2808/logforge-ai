// Phase 7 building blocks: review SLA, confidence evidence, extension storage
// and acting identity. Auxiliary widgets render nothing when their API is
// unavailable, so they never block the page they are embedded in.
import { ReactNode } from "react";
import { getConfidence, getReviews } from "../api/endpoints";
import type { ConfidenceEntry, ReviewSla } from "../api/types";
import { useAuth } from "../lib/auth";
import { fmtTime } from "../lib/format";
import { Link } from "../lib/router";
import { ApiState, useApi } from "../lib/useApi";
import { Badge, Card, KV, Stat } from "./ui";

export function Quiet<T>({ state, children }: { state: ApiState<T>; children: (data: T) => ReactNode }) {
  if (state.error || state.data === null) return null;
  return <>{children(state.data)}</>;
}

export function duration(seconds: number): string {
  const s = Math.abs(seconds);
  const text = s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`;
  return seconds < 0 ? `${text} ago` : `in ${text}`;
}

function SlaDetails({ r }: { r: ReviewSla }) {
  return (
    <div>
      <div className="row"><Badge value={r.status} />{r.escalation_count > 0 && <span className="badge b-fail">escalated ×{r.escalation_count}</span>}
        <span className="small muted">{r.severity} · {r.sla_hours}h SLA</span></div>
      <KV items={[
        ["Review age", duration(-r.review_age_seconds).replace(" ago", "")],
        ["Deadline", `${fmtTime(r.due_at)} (${duration(r.seconds_to_deadline)})`],
        ["Next action", r.next_action],
        ["On timeout", r.fallback],
      ]} />
    </div>
  );
}

/** SLA status for one review item (drift event / onboarding session / learning session). */
export function SlaPanel({ itemType, itemId }: { itemType: string; itemId: string }) {
  const state = useApi((s) => getReviews({ item_type: itemType, item_id: itemId, include_resolved: true }, s), [itemType, itemId]);
  return (
    <Quiet state={state}>
      {(d) => d.items[0] ? (
        <div className="boundary" aria-label="Review SLA">
          <div className="spread" style={{ marginBottom: 6 }}><strong>Review SLA</strong>
            <span className="small muted">A timeout never approves or activates anything.</span></div>
          <SlaDetails r={d.items[0]} />
        </div>
      ) : null}
    </Quiet>
  );
}

/** Who the console acts as for governance actions, and what that means. */
export function ActingAs() {
  const { user } = useAuth();
  return user ? (
    <p className="small muted" style={{ margin: "4px 0 8px" }}>
      Acting as <strong>{user.username}</strong> ({user.role.replace(/_/g, " ")}). Your identity and role are recorded in the
      hash-chained audit log; maker-checker prevents approving a proposal you authored.
    </p>
  ) : (
    <p className="small muted" style={{ margin: "4px 0 8px" }}>
      Not signed in — actions are audited as <strong>anonymous</strong> (RBAC permissive mode).{" "}
      <Link to="governance">Sign in</Link> to act with a role.
    </p>
  );
}

export function ExtensionStorage({ mode, overflow }: { mode?: string | null; overflow?: number | null }) {
  if (!mode) return null;
  return mode === "SPILLED"
    ? <span className="badge b-warn" title="Extensions exceeded the inline budget; the overflow is stored losslessly">SPILLED · {overflow ?? 0} in overflow</span>
    : <span className="badge b-ok">INLINE</span>;
}

function rate(v: number | null | undefined): string {
  return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
}

function Entry({ e }: { e: ConfidenceEntry }) {
  const ev = e.evidence;
  const h = ev.holdout ?? {};
  const m = ev.mutation ?? {};
  const st = ev.structural ?? {};
  const held = h.rederived_offline?.holdout ?? h.historical;
  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, marginTop: 10 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <strong>Proposal v{e.proposal_version}</strong><span className="chip">{e.proposal_source ?? "—"}</span>
        {e.human_decision ? <Badge value={e.human_decision.action} /> : <span className="small faint">no human decision yet</span>}
      </div>
      <div className="stats">
        <Stat label="Stated confidence" value={e.suggestion_confidence === null ? "—" : e.suggestion_confidence.toFixed(2)} hint="as reported by the suggester" />
        <Stat label="Samples" value={e.sample_count} hint={`in-sample ${ev.in_sample?.result ?? "—"} · match ${rate(ev.in_sample?.match_rate)}`} />
        <Stat label="Holdout" value={h.evaluated ? rate(held?.match_rate) : "—"}
          hint={h.evaluated ? (h.split ? `${h.split.holdout} held-out samples (offline re-derivation)` : h.kind) : h.reason} />
        <Stat label="Mutation survival" value={rate(m.robustness_survival_rate)} hint={`fault detection ${rate(m.fault_detection_rate)}`} />
        <Stat label="Structural coverage" value={rate(st.structural_coverage ?? null)}
          hint={st.fields_observed !== undefined ? `${st.fields_mapped ?? "—"}/${st.fields_observed} fields mapped · ${st.fields_preserved ?? "—"} preserved` : undefined} />
        <Stat label="Production outcome" value={e.production_outcome?.measurable ? rate(e.production_outcome.success_rate) : "—"}
          hint={e.production_outcome?.measurable ? `${e.production_outcome.events} events on v${e.production_outcome.version}` : "not measurable yet"} />
      </div>
      {m.operators && (
        <details style={{ marginTop: 8 }}>
          <summary className="small">Mutation operators</summary>
          <table>
            <thead><tr><th>Operator</th><th>Kind</th><th>Result</th></tr></thead>
            <tbody>{Object.entries(m.operators).map(([name, o]) => (
              <tr key={name}><td className="mono small">{name}</td><td className="small">{o.kind}</td>
                <td className="small">{!o.applicable ? "not applicable" : o.kind === "fault" ? `detected ${rate(o.detection_rate)}` : `parsed ${rate(o.match_rate)}`}{o.mutants ? ` of ${o.mutants}` : ""}</td></tr>
            ))}</tbody>
          </table>
        </details>
      )}
    </div>
  );
}

/** Confidence evidence ledger for an onboarding or learning session. */
export function ConfidenceCard({ kind, id, version }: { kind: "onboarding" | "learning"; id: string; version?: number }) {
  const state = useApi((s) => getConfidence(kind, id, s), [kind, id, version]);
  return (
    <Quiet state={state}>
      {(d) => d.entries.length ? (
        <Card title="Confidence evidence">
          <p className="small muted" style={{ marginTop: 0 }}>
            Deterministic evidence behind the stated confidence: holdout, mutation and structural checks run through the real pipeline,
            joined with the human decision and production outcome. An evidence ledger — not a statistical calibration; approval gates are unchanged.
          </p>
          {[...d.entries].reverse().map((e) => <Entry key={e.id} e={e} />)}
        </Card>
      ) : null}
    </Quiet>
  );
}
