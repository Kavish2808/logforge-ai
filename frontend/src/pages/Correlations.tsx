// Cross-vendor drift correlation — an INVESTIGATION aid. It surfaces related
// drift across vendors with a decomposable score; it never changes adapters,
// reviews, learning, golden baselines, rollbacks or events.
import { useState } from "react";
import { analyzeCorrelations, getCorrelation, listCorrelations, type Correlation } from "../api/phase8";
import { ConfirmAction, fmtNum } from "../components/phase8";
import { Badge, Card, KV, Load } from "../components/ui";
import { fmtTime } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { useApi } from "../lib/useApi";

const STRENGTH_CLASS: Record<string, string> = { HIGH: "b-review", MEDIUM: "b-info", LOW: "" };

function Strength({ value }: { value: string }) {
  return <span className={`badge ${STRENGTH_CLASS[value] ?? ""}`} title="Descriptive correlation strength, not a severity">{value} correlation</span>;
}

function Breakdown({ c }: { c: Correlation }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th></tr></thead>
        <tbody>
          {Object.entries(c.score_breakdown).map(([k, v]) => (
            <tr key={k}><td>{k.replace(/_/g, " ")}</td><td>{fmtNum(v.value)}</td><td>{v.weight}</td><td>{fmtNum(v.contribution)}</td></tr>
          ))}
          <tr><td><strong>Total score</strong></td><td /><td /><td><strong>{fmtNum(c.score)}</strong></td></tr>
        </tbody>
      </table>
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const c = useApi((s) => getCorrelation(id, s), [id]);
  return (
    <Card title={<span className="row">Correlation <span className="label-investigation">Investigation only</span></span>}>
      <Load state={c}>
        {(d) => (
          <div className="grid" style={{ gap: 12 }}>
            <div className="row"><Strength value={d.strength} />{d.drift_types.map((t) => <Badge key={t} value={t} />)}</div>
            <p style={{ margin: 0 }}>{d.explanation}</p>
            <KV items={[
              ["Window", `${fmtTime(d.window.start)} → ${fmtTime(d.window.end)}`],
              ["Vendors", d.vendors.join(", ")], ["Sources", d.sources.join(", ")],
              ["Affected fields", d.affected_fields.length ? d.affected_fields.join(", ") : "none shared"],
              ["Change types", (d.change_types ?? []).join(", ")],
            ]} />
            <div><h3>Score breakdown</h3><Breakdown c={d} /></div>
            <div>
              <h3>Per source</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Source</th><th>Vendor</th><th>Fields</th><th>Change types</th><th>Layers</th><th>First seen</th></tr></thead>
                  <tbody>{Object.entries(d.per_source).map(([src, s]) => (
                    <tr key={src}><td className="mono small">{src}</td><td className="small">{s.vendor}</td><td className="small">{s.fields.join(", ")}</td>
                      <td className="small">{s.change_types.join(", ")}</td><td className="small">{s.kinds.join(", ")}</td><td className="small nowrap">{fmtTime(s.first_seen)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
            <div>
              <h3>Evidence</h3>
              <div className="small">
                {d.event_ids.length > 0 && <div>Drift events: {d.event_ids.map((e) => <Link key={e} to="events" param={e}>{e} </Link>)}</div>}
                {d.drift_finding_ids.length > 0 && <div>Findings: {d.drift_finding_ids.map((f) => <code key={f} className="mono-break">{f.slice(0, 16)}… </code>)}
                  {" "}(<Link to="advanced-drift">Advanced Drift</Link>)</div>}
              </div>
            </div>
            <div className="notice">This correlation is evidence for an analyst. It does not modify adapters, accept drift, activate learning, change golden baselines, roll back or alter events.</div>
          </div>
        )}
      </Load>
    </Card>
  );
}

export function Correlations({ correlationId }: { correlationId: string | null }) {
  const [nonce, setNonce] = useState(0);
  const list = useApi((s) => listCorrelations({ limit: 100 }, s), [nonce]);
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Investigate</div>
          <h1 className="row">Cross-vendor correlation <span className="label-investigation">Investigation / analysis</span></h1>
          <p>Related drift from at least two vendors in the same window, scored by source diversity, change overlap, shared fields and time proximity.</p>
        </div>
        <ConfirmAction label="Analyze last 60 minutes"
          description="Groups Phase 5 structural drift and Phase 8 findings from the last 60 minutes by shared fields / change types and stores the correlations (audited). Investigation only — nothing is remediated."
          onConfirm={async () => { const r = await analyzeCorrelations({}); setNonce((n) => n + 1); return `${r.correlations.length} correlation(s) across ${r.sources_with_drift} source(s) with drift.`; }} />
      </div>
      <div className="grid g-main">
        <Card title="Correlation groups">
          <Load state={list} isEmpty={(d) => d.items.length === 0} empty="No cross-vendor correlations. A correlation needs related drift from at least two vendors in one window.">
            {(d) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Window</th><th>Vendors / sources</th><th>Fields</th><th>Score</th><th>Strength</th></tr></thead>
                  <tbody>{d.items.map((c) => (
                    <tr key={c.id} className={`clickable${correlationId === c.id ? " selected" : ""}`} tabIndex={0}
                      onClick={() => navigate("correlations", c.id)} aria-label={`Correlation of ${c.vendors.join(", ")}`}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate("correlations", c.id); }}>
                      <td className="small nowrap">{fmtTime(c.window.end)}</td>
                      <td className="small">{c.vendors.join(", ")}<div className="faint mono-break">{c.sources.join(", ")}</div></td>
                      <td className="small">{c.affected_fields.join(", ") || <span className="faint">none shared</span>}</td>
                      <td>{fmtNum(c.score)}</td>
                      <td><Strength value={c.strength} /></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </Load>
        </Card>
        <div>{correlationId ? <Detail key={correlationId} id={correlationId} /> : <Card><div className="state">Select a correlation to see its evidence and score breakdown.</div></Card>}</div>
      </div>
    </>
  );
}
