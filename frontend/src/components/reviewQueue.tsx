// Phase 8 review queue: unresolved items from the Phase 8 APIs, next to the
// Phase 5 structural queue. Read-only; each item links to where it is decided.
import { getFindings, getGuardComparisons, listCorrelations, listReplayJobs } from "../api/phase8";
import { fmtTime, num } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";
import { fmtNum } from "./phase8";
import { Badge, Card, Load, Tabs } from "./ui";

export function Phase8ReviewQueue() {
  const findings = useApi((s) => getFindings({ status: "OPEN", limit: 200 }, s), []);
  const guards = useApi((s) => getGuardComparisons({ limit: 200 }, s), []);
  const correlations = useApi((s) => listCorrelations({ status: "OPEN", limit: 100 }, s), []);
  const jobs = useApi((s) => listReplayJobs(s), []);
  const count = (n: number | undefined) => (n === undefined ? "…" : n);
  const elevated = guards.data?.items.filter((c) => c.poisoning_risk);
  const waiting = jobs.data?.items.filter((j) => ["PENDING_APPROVAL", "PENDING", "PAUSED"].includes(j.status));

  return (
    <Card title="Phase 8 review queue">
      <p className="small muted" style={{ marginTop: 0 }}>Unresolved statistical / semantic findings, golden-baseline poisoning evidence, open correlations and replays waiting for a human.</p>
      <Tabs tabs={[
        { id: "findings", label: `Findings (${count(findings.data?.items.length)})`, content: (
          <Load state={findings} isEmpty={(d) => d.items.length === 0} empty="No open statistical or semantic findings.">
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Layer</th><th>Source</th><th>Field · metric</th><th>Severity</th><th>Window end</th></tr></thead>
                <tbody>{d.items.map((f) => (
                  <tr key={f.id}><td><Badge value={f.layer} /></td><td className="mono small">{f.source}</td>
                    <td className="small">{f.field} · {f.metric.replace(/_/g, " ")}</td><td><Badge value={f.severity} /></td>
                    <td className="small nowrap"><Link to="advanced-drift">{fmtTime(f.analysis_window.current_end)}</Link></td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Load>
        ) },
        { id: "poisoning", label: `Golden / poisoning (${count(elevated?.length)})`, content: (
          <Load state={guards} isEmpty={() => (elevated ?? []).length === 0} empty="No guarded action has been flagged against a golden baseline.">
            {() => (
              <div className="table-wrap"><table>
                <thead><tr><th>When</th><th>Source</th><th>Action</th><th>Decision</th><th>Why</th></tr></thead>
                <tbody>{elevated!.map((c) => (
                  <tr key={c.id}><td className="small nowrap">{fmtTime(c.created_at)}</td>
                    <td className="mono small"><Link to="baselines" param={c.source_key}>{c.source_key}</Link></td>
                    <td className="small"><span className="chip">{c.action}</span></td><td><Badge value={c.decision} /></td>
                    <td className="small">{c.risk_reasons.map((r) => <div key={r.code}>{r.detail}</div>)}
                      <div className="faint">new vs golden {fmtNum(c.new_vs_golden?.similarity ?? null)}</div></td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Load>
        ) },
        { id: "correlations", label: `Correlations (${count(correlations.data?.items.length)})`, content: (
          <Load state={correlations} isEmpty={(d) => d.items.length === 0} empty="No open cross-vendor correlations.">
            {(d) => (
              <div className="table-wrap"><table>
                <thead><tr><th>Window end</th><th>Vendors</th><th>Fields</th><th>Strength</th></tr></thead>
                <tbody>{d.items.map((c) => (
                  <tr key={c.id}><td className="small nowrap"><Link to="correlations" param={c.id}>{fmtTime(c.window.end)}</Link></td>
                    <td className="small">{c.vendors.join(", ")}</td><td className="small">{c.affected_fields.join(", ") || "—"}</td>
                    <td><span className="small">{c.strength} · {fmtNum(c.score)}</span></td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Load>
        ) },
        { id: "replay", label: `Replays waiting (${count(waiting?.length)})`, content: (
          <Load state={jobs} isEmpty={() => (waiting ?? []).length === 0} empty="No replay job is waiting to be started or resumed.">
            {() => (
              <div className="table-wrap"><table>
                <thead><tr><th>Job</th><th>Status</th><th>Events</th><th>Created by</th></tr></thead>
                <tbody>{waiting!.map((j) => (
                  <tr key={j.id}><td className="small"><Link to="replay" param={j.id}>{j.adapter_id}</Link></td><td><Badge value={j.status} /></td>
                    <td className="small">{num(j.total)}</td><td className="small">{j.created_by}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Load>
        ) },
      ]} />
    </Card>
  );
}
