// Shadow validation for one learning session: OLD (current adapter) vs NEW
// (candidate) on real stored events, per stratum, with the circuit-breaker
// verdict. Everything shown is the stored shadow run; the activation gate is
// enforced by the server, this panel only explains it.
import { useState } from "react";
import type { LearningSession } from "../api/types";
import { createShadowRun, getShadowRun, listShadowRuns, type ShadowRun } from "../api/phase8";
import { Check, ConfirmAction, ShadowVerdict, fmtNum } from "../components/phase8";
import { Badge, Card, KV, Load, Stat } from "../components/ui";
import { fmtTime, pct } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";

const ELIGIBILITY: Record<string, string> = {
  PASSED: "The shadow gate permits activation. RBAC, maker-checker and the golden-baseline guard still apply.",
  REVIEW_REQUIRED: "Activation is possible only as an elevated action: an authenticated SOC_ADMIN with a written note.",
  BLOCKED: "Activation will be refused by the server (409) while this is the latest run for the current proposal.",
};

function Eligibility({ run, session }: { run: ShadowRun; session: LearningSession }) {
  const current = run.proposal_version === session.proposal_version;
  return (
    <div className={`notice ${!current ? "" : run.verdict === "PASSED" ? "ok" : run.verdict === "BLOCKED" ? "fail" : "warn"}`}>
      <strong>Activation eligibility: </strong>
      {current
        ? ELIGIBILITY[run.verdict] ?? run.verdict
        : `This run validated proposal v${run.proposal_version}; the current proposal is v${session.proposal_version}, so it does not gate activation.`}
      <div className="small" style={{ marginTop: 4 }}>
        Enforced server-side by the Phase 8 shadow gate (server setting PHASE8_SHADOW_GATE; default <code>if_present</code>: without a run, Phase 6 activation rules apply unchanged).
      </div>
    </div>
  );
}

function RunDetail({ id, session }: { id: string; session: LearningSession }) {
  const run = useApi((s) => getShadowRun(id, s), [id]);
  return (
    <Load state={run}>
      {(r) => {
        const t = r.summary.totals;
        const n = r.sample_count;
        return (
          <div className="grid" style={{ gap: 14 }}>
            <ShadowVerdict verdict={r.verdict} />
            <Eligibility run={r} session={session} />
            {r.reasons.length > 0 && (
              <div>
                <h3>{r.breaker_tripped ? "Circuit breaker" : "Why not PASSED"}</h3>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {r.reasons.map((x) => (
                    <li key={x.code}>
                      <span className={`badge ${x.critical ? "b-fail" : "b-warn"}`}>{x.code.replace(/_/g, " ")}</span>{" "}
                      {x.detail}{x.count !== undefined ? ` — ${x.count} event(s)` : ""}
                      {x.old_p95_ms !== undefined && ` (p95 ${x.old_p95_ms} → ${x.new_p95_ms} ms)`}
                      {x.event_ids && x.event_ids.length > 0 && (
                        <div className="faint">e.g. {x.event_ids.slice(0, 3).map((e) => <Link key={e} to="events" param={e}>{e} </Link>)}</div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="stats">
              <Stat label="Sampled events" value={n} hint={`target ${r.thresholds.stratum_target ?? 25} per stratum`} />
              <Stat label="Parse success OLD" value={t ? `${t.old_parse_success}/${n}` : "—"} hint={t ? pct(t.old_parse_success, n) : undefined} />
              <Stat label="Parse success NEW" value={t ? `${t.new_parse_success}/${n}` : "—"} hint={t ? pct(t.new_parse_success, n) : undefined} />
              <Stat label="Evidence loss" value={t ? t.evidence_loss : "—"} hint="fields no longer accounted for" />
              <Stat label="Raw hash" value={t ? <Check ok={t.raw_hash_mismatches === 0} yes="unchanged" no={`${t.raw_hash_mismatches} mismatch`} /> : "—"} />
              <Stat label="Status changes" value={t ? t.status_changes : "—"} />
              <Stat label="Drift differences" value={t ? t.drift_differences : "—"} />
              <Stat label="Improvements" value={t ? t.improvements : "—"} hint="never blocking" />
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Latency ({r.latency.unit ?? "ms"})</th><th>p50</th><th>p95</th></tr></thead>
                <tbody>
                  <tr><td>OLD (current)</td><td>{fmtNum(r.latency.old?.p50)}</td><td>{fmtNum(r.latency.old?.p95)}</td></tr>
                  <tr><td>NEW (candidate)</td><td>{fmtNum(r.latency.new?.p50)}</td><td>{fmtNum(r.latency.new?.p95)}</td></tr>
                </tbody>
              </table>
              <p className="small faint" style={{ margin: "4px 0 0" }}>
                Blocked when NEW p95 &gt; {r.thresholds.latency_ratio ?? 3}× OLD p95 and at least {r.thresholds.latency_floor_ms ?? 0.5} ms slower;
                median of {r.latency.repeats_per_event ?? 3} interleaved runs per event.
              </p>
            </div>
            <div>
              <h3>Stratum coverage</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Stratum</th><th>Selected</th><th>Available</th><th>Parse OLD → NEW</th><th>Critical</th><th>Review</th><th>Improved</th><th>Unchanged</th></tr></thead>
                  <tbody>{Object.entries(r.strata).map(([name, s]) => (
                    <tr key={name}>
                      <td><span className="mono small">{name}</span>{s.rule && <div className="faint small">{s.rule}</div>}</td>
                      <td>{s.selected.length}/{s.target} {s.sufficient ? <Badge value="OK" /> : <span className="badge b-warn">insufficient</span>}</td>
                      <td>{s.available}</td>
                      <td className="small">{s.results ? `${s.results.old_parse_success} → ${s.results.new_parse_success}` : "—"}</td>
                      <td>{s.results?.critical ?? "—"}</td><td>{s.results?.review ?? "—"}</td>
                      <td>{s.results?.improvements ?? "—"}</td><td>{s.results?.unchanged ?? "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
            <details>
              <summary className="small">OLD vs NEW evidence — {(r.diff ?? []).length} event(s) with differences</summary>
              {(r.diff ?? []).length === 0 ? <p className="small faint">Every sampled event produced identical results.</p> : (
                <div className="table-wrap" style={{ marginTop: 8 }}>
                  <table>
                    <thead><tr><th>Event</th><th>Stratum</th><th>OLD</th><th>NEW</th><th>Changes</th></tr></thead>
                    <tbody>{r.diff!.map((d) => (
                      <tr key={d.event_id}>
                        <td className="small"><Link to="events" param={d.event_id}>{d.event_id}</Link></td>
                        <td className="mono small">{d.stratum}</td>
                        <td className="small"><Badge value={d.old.status} /> <span className="mono">{d.old.adapter ?? "—"} v{d.old.version ?? "—"}</span><div className="faint">drift {d.old.drift ?? "—"}</div></td>
                        <td className="small"><Badge value={d.new.status} /> <span className="mono">{d.new.adapter ?? "—"} v{d.new.version ?? "—"}</span><div className="faint">drift {d.new.drift ?? "—"}</div></td>
                        <td className="small">{d.changes.map((c, i) => (
                          <div key={i}><span className={`chip ${["STATUS_DEGRADED", "EVIDENCE_LOSS", "RAW_HASH_MISMATCH"].includes(c.kind) ? "rem" : ["NEWLY_MAPPED", "EXTENSION_PROMOTED", "STATUS_IMPROVED", "WARNINGS_REMOVED"].includes(c.kind) ? "add" : "chg"}`}>{c.kind}</span>
                            {" "}{Object.entries(c).filter(([k]) => k !== "kind").map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ")}</div>
                        ))}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </details>
            <KV items={[
              ["Run", <code key="i">{r.id}</code>], ["Current → candidate", `v${r.current_version ?? "—"} → v${r.candidate_version ?? "—"}`],
              ["Proposal", `v${r.proposal_version}`], ["Policy", r.thresholds.policy],
              ["Started", fmtTime(r.summary.started_at)], ["Finished", fmtTime(r.summary.finished_at)],
              ["Duration", r.summary.duration_ms !== undefined ? `${r.summary.duration_ms} ms` : null], ["Run by", r.created_by],
            ]} />
          </div>
        );
      }}
    </Load>
  );
}

export function ShadowPanel({ session }: { session: LearningSession }) {
  const runs = useApi((s) => listShadowRuns(session.id, s), [session.id]);
  const [selected, setSelected] = useState<string | null>(null);
  const latest = runs.data?.items[0]?.id ?? null;
  const shown = selected ?? latest;
  return (
    <Card title={<span className="row">Shadow validation &amp; circuit breaker {runs.data?.items[0] && <Badge value={runs.data.items[0].verdict} />}</span>}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Replays up to 25 real stored events per stratum through the current adapter and the candidate in a throwaway context.
        Production events, adapters and baselines are never modified.
      </p>
      {session.candidate ? (
        <ConfirmAction label="Run shadow validation"
          description={`Compares the candidate of proposal v${session.proposal_version} with the active adapter. Stores a shadow run (audited); changes nothing in production.`}
          onConfirm={async () => { const r = await createShadowRun(session.id); setSelected(r.id); runs.reload(); return `Shadow run finished: ${r.verdict.replace(/_/g, " ")}.`; }} />
      ) : <div className="small faint">This session has no candidate adapter to validate.</div>}
      <div style={{ marginTop: 12 }}>
        <Load state={runs} isEmpty={(d) => d.items.length === 0} empty="No shadow run yet for this session.">
          {(d) => (
            <>
              {d.items.length > 1 && (
                <div className="row" style={{ marginBottom: 10 }}>
                  <label className="field">Run
                    <select value={shown ?? ""} onChange={(e) => setSelected(e.target.value)} aria-label="Shadow run">
                      {d.items.map((r) => <option key={r.id} value={r.id}>{fmtTime(r.created_at)} · {r.verdict} · proposal v{r.proposal_version}</option>)}
                    </select>
                  </label>
                </div>
              )}
              {shown && <RunDetail key={shown} id={shown} session={session} />}
            </>
          )}
        </Load>
      </div>
    </Card>
  );
}
