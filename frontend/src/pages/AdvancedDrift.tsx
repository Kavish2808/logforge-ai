// Advanced Drift — the three layers side by side, all from existing APIs:
// Phase 5 structural drift (/views/events), Phase 8 statistical findings and
// semantic advisories (/drift/findings), plus each source's golden-baseline state.
import { useState } from "react";
import { getEvents } from "../api/endpoints";
import {
  acknowledgeFinding, getFinding, getFindings, getGoldenBaselines, runStatisticalAnalysis,
  type DriftFinding, type GoldenBaseline,
} from "../api/phase8";
import { ConfirmAction, fmtNum } from "../components/phase8";
import { ActingAs } from "../components/trust";
import { Badge, Card, Json, KV, Load, Tabs } from "../components/ui";
import { fmtTime } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";

function GoldenRef({ source, golden }: { source: string; golden: Record<string, GoldenBaseline> | null }) {
  if (!golden) return <span className="faint">—</span>;
  const g = golden[source];
  return g
    ? <Link to="baselines" param={source}><Badge value="ACTIVE" title="Active golden baseline" /> golden v{g.version}</Link>
    : <span className="small faint">no golden</span>;
}

function FindingDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const f = useApi((s) => getFinding(id, s), [id]);
  return (
    <Load state={f}>
      {(d) => (
        <div className="grid" style={{ gap: 12 }}>
          <div className="row"><Badge value={d.layer} /><Badge value={d.severity} /><Badge value={d.status} />
            <span className="mono small">{d.source} · {d.field} · {d.metric}</span></div>
          <p style={{ margin: 0 }}>{d.explanation}</p>
          <KV items={[
            ["Deviation", `${fmtNum(d.deviation)} (threshold ${fmtNum(d.threshold)})`],
            ["Deterministic reason", <code key="r">{d.deterministic_reason}</code>],
            ["Evidence counts", `${d.evidence_counts.baseline_events} baseline · ${d.evidence_counts.current_events} current events`],
            ["Quality", d.quality],
            ["Baseline window", `${fmtTime(d.analysis_window.baseline_start)} → ${fmtTime(d.analysis_window.baseline_end)}`],
            ["Current window", `${fmtTime(d.analysis_window.current_start)} → ${fmtTime(d.analysis_window.current_end)}`],
            ["Parent finding", d.parent_finding_id ? <code key="p" className="mono-break">{d.parent_finding_id}</code> : null],
            ["Acknowledged by", d.acknowledged_by],
          ]} />
          <div className="grid g2">
            <div><h3>Baseline value</h3><Json value={d.baseline_value} /></div>
            <div><h3>Current value</h3><Json value={d.current_value} /></div>
          </div>
          {(d.children ?? []).length > 0 && (
            <div>
              <h3>Semantic advisories built on this finding</h3>
              {d.children!.map((c) => <div key={c.id} className="notice" style={{ marginTop: 6 }}><Badge value="ADVISORY" /> {c.metric}: {c.explanation}</div>)}
            </div>
          )}
          {d.status === "OPEN" && (
            <div>
              <ActingAs />
              <ConfirmAction label="Acknowledge finding" noteLabel="Review note"
                description="Records that a human reviewed this finding (audited). It does not change any baseline, adapter or event."
                onConfirm={async (note) => { await acknowledgeFinding(d.id, note || null); f.reload(); onChanged(); return "Finding acknowledged."; }} />
            </div>
          )}
        </div>
      )}
    </Load>
  );
}

function FindingsTable({ layer, golden }: { layer: "STATISTICAL" | "SEMANTIC"; golden: Record<string, GoldenBaseline> | null }) {
  const [status, setStatus] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const findings = useApi((s) => getFindings({ layer, status: status || undefined, limit: 200 }, s), [layer, status]);
  return (
    <div className="grid g-main">
      <div>
        <div className="row" style={{ marginBottom: 8 }}>
          <label className="field">Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setSelected(null); }} aria-label={`${layer} status filter`}>
              <option value="">All</option><option value="OPEN">Open</option><option value="ACKNOWLEDGED">Acknowledged</option>
            </select>
          </label>
        </div>
        <Load state={findings} isEmpty={(d) => d.items.length === 0}
          empty={layer === "STATISTICAL"
            ? "No statistical findings. Findings appear after an analysis over windows with at least 200 events each."
            : "No semantic advisories. They are only produced on top of a statistical finding."}>
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Source / golden</th><th>Field</th><th>Metric</th><th>Severity</th><th>Baseline → current window</th><th>Status</th></tr></thead>
                <tbody>
                  {d.items.map((f: DriftFinding) => (
                    <tr key={f.id} className={`clickable${selected === f.id ? " selected" : ""}`} tabIndex={0}
                      onClick={() => setSelected(f.id)} aria-label={`Finding ${f.metric} on ${f.field}`}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(f.id); } }}>
                      <td><div className="mono">{f.source}</div><GoldenRef source={f.source} golden={golden} /></td>
                      <td className="mono small">{f.field}</td>
                      <td className="small">{f.metric.replace(/_/g, " ")}<div className="faint">{fmtNum(f.deviation)} / {fmtNum(f.threshold)}</div></td>
                      <td><Badge value={f.severity} /></td>
                      <td className="small nowrap">{fmtTime(f.analysis_window.current_end)}<div className="faint">{f.evidence_counts.baseline_events} → {f.evidence_counts.current_events} events</div></td>
                      <td><Badge value={f.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </div>
      <Card title="Finding">
        {selected ? <FindingDetail key={selected} id={selected} onChanged={findings.reload} /> : <div className="state">Select a finding to see its evidence.</div>}
      </Card>
    </div>
  );
}

function Structural({ golden }: { golden: Record<string, GoldenBaseline> | null }) {
  const drift = useApi((s) => getEvents({ drift_status: "DRIFT", limit: 100 }, s), []);
  const possible = useApi((s) => getEvents({ drift_status: "POSSIBLE_FORMAT_DRIFT", limit: 100 }, s), []);
  return (
    <Load state={drift}>
      {(d) => {
        const rows = [...d.items, ...(possible.data?.items ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at));
        return rows.length === 0 ? <div className="state">No structural drift has been detected.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source / golden</th><th>Vendor</th><th>Drift</th><th>Event status</th><th>Received</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.event_id}>
                    <td><div className="mono">{r.source_key ?? "—"}</div>{r.source_key && <GoldenRef source={r.source_key} golden={golden} />}</td>
                    <td className="small">{r.vendor ?? "—"}</td>
                    <td><Badge value={r.drift_status} />{r.drift_severity && <> <Badge value={r.drift_severity} /></>}</td>
                    <td><Badge value={r.status} /></td>
                    <td className="small nowrap">{fmtTime(r.received_at)}</td>
                    <td className="small"><Link to="events" param={r.event_id}>Forensics</Link> · <Link to="drift">Review</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {possible.error && <div className="notice warn" style={{ marginTop: 8 }}>Possible format drift list unavailable: {possible.error}</div>}
          </div>
        );
      }}
    </Load>
  );
}

export function AdvancedDrift() {
  const golden = useApi((s) => getGoldenBaselines(s), []);
  const goldenBySource = golden.data ? Object.fromEntries(golden.data.items.map((g) => [g.source_key, g])) : null;
  const all = useApi((s) => getFindings({ limit: 1 }, s), []);
  const [nonce, setNonce] = useState(0);
  const stats = all.data?.stats.by_layer_status ?? {};
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Adapt · change detection</div>
          <h1>Advanced Drift</h1>
          <p>Structural (Phase 5) → statistical → semantic advisory. Deterministic evidence only; nothing here changes a baseline or adapter.</p>
        </div>
        <ConfirmAction label="Run statistical analysis"
          description="Analyzes every source active in the last completed hour against the previous 168 hours (≥ 200 events per window) and stores findings. Audited; changes no baseline, adapter or event."
          onConfirm={async () => {
            const r = await runStatisticalAnalysis({});
            setNonce((n) => n + 1);
            all.reload();
            return `${r.sources_analyzed} source(s) analyzed: ${r.findings} finding(s), ${r.advisories} advisory(ies).`;
          }} />
      </div>
      <div className="stats">
        {["STATISTICAL:OPEN", "STATISTICAL:ACKNOWLEDGED", "SEMANTIC:OPEN", "SEMANTIC:ACKNOWLEDGED"].map((k) => (
          <div key={k} className="stat"><div className="label">{k.replace(":", " · ").toLowerCase()}</div><div className="value">{all.data ? stats[k] ?? 0 : "…"}</div></div>
        ))}
        <div className="stat"><div className="label">active golden baselines</div><div className="value">{golden.data?.total ?? "…"}</div></div>
      </div>
      {golden.error && <div className="notice warn">Golden baseline state unavailable: {golden.error}</div>}
      <Card>
        <Tabs key={nonce} tabs={[
          { id: "statistical", label: "Statistical", content: <FindingsTable layer="STATISTICAL" golden={goldenBySource} /> },
          { id: "semantic", label: "Semantic (advisory)", content: <FindingsTable layer="SEMANTIC" golden={goldenBySource} /> },
          { id: "structural", label: "Structural (Phase 5)", content: <Structural golden={goldenBySource} /> },
        ]} />
      </Card>
    </>
  );
}
