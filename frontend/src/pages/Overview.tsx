// Dashboard — operational intelligence. Every number is read from the Views API; derived rates show their basis.
import { useState } from "react";
import {
  Activity, AlertTriangle, Boxes, Braces, Brain, CheckCircle2, CircleDashed, Database, Fingerprint, GitBranch, Layers, Radar,
  ShieldCheck, Workflow, XCircle,
} from "lucide-react";
import { getEvents, getSources, getSummary, getTrust, listLearning } from "../api/endpoints";
import type { SourceSummary, Summary, TrustSummary } from "../api/types";
import { AreaChart, Donut, Sparkline } from "../components/charts";
import { EventTable } from "../components/EventTable";
import { Quiet } from "../components/trust";
import { Badge, Bars, Card, EmptyState, Kpi, Load, Stat, STATUS_COLORS } from "../components/ui";
import { fmtAgo, fmtTime, num, sum } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";

const rate = (part: number, total: number) => (total ? (part / total) * 100 : null);
const fmtRate = (r: number | null) => (r === null ? "—" : `${r >= 99.95 || r === 0 ? r.toFixed(0) : r.toFixed(1)}%`);
const tone = (r: number | null): "ok" | "warn" | "fail" | "na" => (r === null ? "na" : r >= 99 ? "ok" : r >= 90 ? "warn" : "fail");

function Delta({ trend }: { trend: Summary["trend"] }) {
  if (trend.length < 2) return null;
  const last = trend[trend.length - 1].total;
  const prev = trend[trend.length - 2].total;
  if (!prev && !last) return null;
  const d = last - prev;
  return <span className={`delta ${d > 0 ? "up" : d < 0 ? "down" : ""}`} title="Latest bucket compared with the previous one">{d > 0 ? "▲" : d < 0 ? "▼" : "■"} {num(Math.abs(d))} vs prev.</span>;
}

type Health = "Healthy" | "Needs review" | "Drifted" | "Parse failures";
function classify(s: SourceSummary): Health {
  if (s.under_review > 0) return "Needs review";
  if ((s.drift.DRIFT ?? 0) + (s.drift.POSSIBLE_FORMAT_DRIFT ?? 0) > 0) return "Drifted";
  if ((s.events.FAILED ?? 0) > 0) return "Parse failures";
  return "Healthy";
}
const HEALTH_META: Record<Health, { icon: typeof CheckCircle2; color: string; bg: string; basis: string }> = {
  Healthy: { icon: CheckCircle2, color: "var(--ok)", bg: "var(--ok-bg)", basis: "no drift, nothing under review, no parse failures" },
  "Needs review": { icon: CircleDashed, color: "var(--review)", bg: "var(--review-bg)", basis: "events awaiting a human drift decision" },
  Drifted: { icon: AlertTriangle, color: "var(--warn)", bg: "var(--warn-bg)", basis: "structural drift observed (decided)" },
  "Parse failures": { icon: XCircle, color: "var(--fail)", bg: "var(--fail-bg)", basis: "events that failed to parse (raw preserved)" },
};

function AdapterHealth() {
  const sources = useApi((s) => getSources(s), []);
  return (
    <Card title={<><Boxes size={16} aria-hidden="true" /> Adapter health</>} actions={<Link to="sources">Sources →</Link>}>
      <Load state={sources} isEmpty={(d) => d.items.length === 0} empty="No sources observed yet — ingest logs to see adapter health.">
        {(d) => {
          const groups: Record<Health, SourceSummary[]> = { Healthy: [], "Needs review": [], Drifted: [], "Parse failures": [] };
          d.items.forEach((s) => groups[classify(s)].push(s));
          return (
            <>
              <div className="health-list">
                {(Object.keys(groups) as Health[]).map((h) => {
                  const m = HEALTH_META[h];
                  const Icon = m.icon;
                  return (
                    <div className="health-item" key={h} title={groups[h].map((s) => s.source_key).join(", ") || undefined}>
                      <span className="hi-icon" style={{ background: m.bg, color: m.color }}><Icon size={15} aria-hidden="true" /></span>
                      <div><strong>{h}</strong><div className="hi-meta">{m.basis}</div></div>
                      <strong style={{ fontSize: 18, fontVariantNumeric: "tabular-nums" }}>{groups[h].length}</strong>
                    </div>
                  );
                })}
              </div>
              <p className="small faint" style={{ marginTop: 12 }}>{d.items.length} source(s), each counted once by its most severe condition.</p>
            </>
          );
        }}
      </Load>
    </Card>
  );
}

function PipelineHealth({ s, t }: { s: Summary; t: TrustSummary | null }) {
  const total = s.totals.events;
  const unknown = s.by_format.unknown ?? 0;
  const failed = s.by_status.FAILED ?? 0;
  const success = s.by_status.SUCCESS ?? 0;
  const partial = s.by_status.PARTIAL ?? 0;
  const learning = sum(s.learning_sessions);
  const stages = [
    { name: "Detect", icon: Radar, r: rate(total - unknown, total), basis: `${num(total - unknown)} of ${num(total)} with a recognized format` },
    { name: "Parse", icon: Braces, r: rate(total - failed, total), basis: `${num(failed)} failed · raw preserved` },
    { name: "Normalize", icon: Layers, r: rate(success, total), basis: `${num(success)} full · ${num(partial)} partial (values preserved)` },
    t ? { name: "Preserve", icon: Database, r: rate(t.raw_vault.cold_stored, t.raw_vault.events_total), basis: `${num(t.raw_vault.cold_stored)} in cold vault · ${num(t.raw_vault.cold_failed)} failed` }
      : { name: "Preserve", icon: Database, r: null, basis: "vault summary unavailable" },
    t ? { name: "Verify", icon: Fingerprint, r: rate(t.integrity.events_sealed, t.integrity.events_sealed + t.integrity.events_unsealed), basis: `${num(t.integrity.events_sealed)} Merkle-sealed · ${num(t.integrity.events_unsealed)} awaiting seal` }
      : { name: "Verify", icon: Fingerprint, r: null, basis: "integrity summary unavailable" },
    { name: "Learn", icon: Brain, r: null, basis: learning ? Object.entries(s.learning_sessions).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") : "no learning sessions yet", count: learning },
  ];
  return (
    <Card title={<><Workflow size={16} aria-hidden="true" /> Pipeline health</>} actions={<span className="small faint">share of events that completed each stage</span>}>
      <div className="pipe" role="list">
        {stages.map((st) => {
          const Icon = st.icon;
          const tn = "count" in st ? (st.count ? "ok" : "na") : tone(st.r);
          return (
            <div key={st.name} className={`pipe-stage s-${tn}`} role="listitem">
              <div className="ps-top"><span className="ps-icon"><Icon size={15} aria-hidden="true" /></span><span className="ps-name">{st.name}</span></div>
              <div className="ps-value">{"count" in st ? num(st.count) : fmtRate(st.r)}</div>
              <div className="ps-basis">{st.basis}</div>
              <div className="ps-bar" aria-hidden="true"><i style={{ width: `${"count" in st ? (st.count ? 100 : 0) : st.r ?? 0}%` }} /></div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function RecentDrift() {
  const drift = useApi((s) => getEvents({ drift_status: "DRIFT", limit: 6 }, s), []);
  return (
    <Card title={<><Activity size={16} aria-hidden="true" /> Recent drift findings</>} actions={<Link to="advanced-drift">Advanced drift →</Link>}>
      <Load state={drift} isEmpty={(d) => d.items.length === 0}
        empty="LogForge has not recorded structural drift on any event yet.">
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th>Vendor</th><th>Drift</th><th>Severity</th><th>Status</th><th>Detected</th></tr></thead>
              <tbody>{d.items.map((e) => (
                <tr key={e.event_id}>
                  <td><Link to="events" param={e.event_id} className="mono">{e.source_key ?? "—"}</Link></td>
                  <td>{e.vendor ?? <span className="faint">—</span>}</td>
                  <td><Badge value={e.drift_status} /></td>
                  <td><Badge value={e.drift_severity} /></td>
                  <td><Badge value={e.status} /></td>
                  <td className="small nowrap" title={fmtTime(e.received_at)}>{fmtAgo(e.received_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Load>
    </Card>
  );
}

export function Overview() {
  const [bucket, setBucket] = useState<"hour" | "day">("hour");
  const summary = useApi((s) => getSummary({ bucket }, s), [bucket]);
  const recent = useApi((s) => getEvents({ limit: 8 }, s), []);
  const queue = useApi((s) => getEvents({ status: "UNDER_REVIEW", limit: 5 }, s), []);
  const sources = useApi((s) => getSources(s), []);
  const learning = useApi((s) => listLearning({ limit: 5 }, s), []);
  const trust = useApi((s) => getTrust(s), []);
  const t = trust.error ? null : trust.data;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Operational Metrics</div>
          <h1>Overview</h1>
          <p>Real-time log volume, normalization rates, adapter status, and pipeline health.</p>
        </div>
        <div className="row">
          {recent.data?.items[0] && <span className="pill"><span className="dot ok" style={{ marginRight: 0 }} />Last event {fmtAgo(recent.data.items[0].received_at)}</span>}
          <div className="row" role="group" aria-label="Trend bucket" style={{ gap: 4 }}>
            {(["hour", "day"] as const).map((b) => (
              <button key={b} className={bucket === b ? "primary" : ""} onClick={() => setBucket(b)}>per {b}</button>
            ))}
          </div>
        </div>
      </div>

      <Load state={summary}>
        {(s) => {
          const total = s.totals.events;
          const st = (k: string) => s.by_status[k] ?? 0;
          const adapters = sum(s.adapters);
          const norm = rate(st("SUCCESS"), total);
          const sealed = t ? rate(t.integrity.events_sealed, t.integrity.events_sealed + t.integrity.events_unsealed) : null;
          return (
            <>
              <div className="kpis">
                <Kpi label="Total events" value={num(total)} icon={Activity} hint={<><Delta trend={s.trend} /> <span>{num(s.totals.unique_sources)} sources · {num(s.totals.unique_vendors)} vendors</span></>}>
                  <Sparkline values={s.trend.map((b) => b.total)} />
                </Kpi>
                <Kpi label="Normalization rate" value={fmtRate(norm)} icon={Layers} tone={norm === null ? undefined : norm >= 99 ? "ok" : norm >= 90 ? "warn" : "fail"}
                  hint={`${num(st("SUCCESS"))} fully normalized · ${num(st("PARTIAL"))} partial`} />
                <Kpi label="Active adapters" value={num(adapters)} icon={GitBranch} tone="cyan"
                  hint={`${s.adapters.shipped_vendor ?? 0} vendor · ${s.adapters.shipped_generic ?? 0} generic · ${s.adapters.onboarded_active ?? 0} onboarded`} />
                <Kpi label="Drift events" value={num(s.totals.drift_events)} icon={AlertTriangle} tone={s.totals.drift_events ? "warn" : "ok"}
                  hint={`${num(s.totals.pending_reviews ?? st("UNDER_REVIEW"))} awaiting review`} />
                <Kpi label="Evidence sealed" value={fmtRate(sealed)} icon={ShieldCheck} tone={sealed === null ? undefined : sealed >= 99 ? "ok" : "warn"}
                  hint={t ? `${num(t.integrity.events_sealed)} Merkle-sealed · ${num(t.raw_vault.cold_failed)} vault failure(s)` : "integrity summary unavailable"} />
              </div>

              <div className="grid g-main" style={{ marginTop: 16 }}>
                <Card title={<><Activity size={16} aria-hidden="true" /> Event processing trend</>} actions={<span className="small faint">stacked by processing status</span>}>
                  {s.trend.length
                    ? <AreaChart buckets={s.trend} bucket={bucket} />
                    : <EmptyState icon={Activity} title="No events in this window">Ingest logs to see processing volume over time.</EmptyState>}
                  <div className="legend" style={{ marginTop: 10 }}>
                    {Object.entries(STATUS_COLORS).map(([k, c]) => (
                      <span key={k}><i style={{ background: c }} />{k.replace("_", " ")} <b className="faint">{num(st(k))}</b></span>
                    ))}
                  </div>
                </Card>
                <Card title={<><Braces size={16} aria-hidden="true" /> Format distribution</>}>
                  {Object.keys(s.by_format).length
                    ? <Donut data={s.by_format} label="events" />
                    : <EmptyState icon={Braces}>No formats detected yet.</EmptyState>}
                </Card>
              </div>

              <PipelineHealth s={s} t={t} />

              <div className="grid g3">
                <AdapterHealth />
                <Card title="Status distribution">
                  <Bars data={s.by_status} colorFor={(k) => STATUS_COLORS[k] ?? "#4f7cff"} />
                  <h3 style={{ marginTop: 18 }}>Drift decisions</h3>
                  <Bars data={s.by_drift_status} />
                </Card>
                <Card title="Onboarding & learning">
                  <div className="stats" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
                    <Stat label="Onboarding sessions" value={num(sum(s.onboarding_sessions))}
                      hint={Object.entries(s.onboarding_sessions).map(([k, v]) => `${v} ${k.toLowerCase().replace(/_/g, " ")}`).join(" · ") || "none yet"} />
                    <Stat label="Learning sessions" value={num(sum(s.learning_sessions))}
                      hint={Object.entries(s.learning_sessions).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "none yet"} />
                  </div>
                  <div className="row" style={{ marginTop: 12 }}>
                    <Link to="onboarding" className="button small">Onboard a source</Link>
                    <Link to="learning" className="button small">Learning queue</Link>
                  </div>
                </Card>
              </div>
            </>
          );
        }}
      </Load>

      <RecentDrift />

      <Quiet state={trust}>
        {(tr) => (
          <Card className="evidence" title={<><Fingerprint size={16} aria-hidden="true" /> Trust & governance</>} actions={<Link to="integrity">Integrity →</Link>}>
            <div className="stats">
              <Stat label="Sealed (Merkle)" value={num(tr.integrity.events_sealed)} hint={`${num(tr.integrity.events_unsealed)} awaiting seal`} />
              <Stat label="Raw hot / cold" value={`${num(tr.raw_vault.hot)} / ${num(tr.raw_vault.cold_stored)}`} hint={`${num(tr.raw_vault.cold_failed)} vault failure(s)`} />
              <Stat label="Spilled extensions" value={num(tr.extension_overflow.events_spilled)} hint={`${num(tr.extension_overflow.overflow_field_count)} fields in overflow`} />
              <Stat label="Open reviews" value={num(tr.reviews.open)} hint={`${num((tr.reviews.by_status.OVERDUE ?? 0) + (tr.reviews.by_status.ESCALATED ?? 0))} overdue/escalated`} />
              <Stat label="Open alerts" value={num(tr.alerts.by_status.OPEN ?? 0)} hint={`${num(tr.alerts.unread)} unread`} />
              <Stat label="Audit records" value={num(tr.audit.total)} hint={tr.audit.head ? `head #${tr.audit.head.seq}` : "none yet"} />
              <Stat label="Confidence ledger" value={num(tr.confidence.total)} hint="suggestions with evidence" />
              <Stat label="Exports" value={num(tr.exports.exports)} hint={`${num(tr.exports.rows_exported)} rows`} />
            </div>
          </Card>
        )}
      </Quiet>

      <div className="grid g2">
        <Card title="Drift queue" actions={<Link to="drift">Open queue →</Link>}>
          <Load state={queue} isEmpty={(d) => d.items.length === 0} empty="No events are awaiting review.">
            {(d) => <EventTable rows={d.items} compact />}
          </Load>
        </Card>
        <Card title="Top sources" actions={<Link to="sources">All sources →</Link>}>
          <Load state={sources} isEmpty={(d) => d.items.length === 0} empty="No sources yet.">
            {(d) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Source</th><th>Kind</th><th>Events</th><th>Version</th><th>Last seen</th></tr></thead>
                  <tbody>
                    {[...d.items].sort((a, b) => (b.events.total ?? 0) - (a.events.total ?? 0)).slice(0, 6).map((src) => (
                      <tr key={src.source_key}>
                        <td><Link to="sources" param={src.source_key} className="mono">{src.source_key}</Link></td>
                        <td className="small">{src.kind.replace("_", " ")}</td>
                        <td>{num(src.events.total)}</td>
                        <td className="mono">{src.active_version ? `v${src.active_version}` : "—"}</td>
                        <td className="small">{fmtAgo(src.last_seen)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Load>
        </Card>
      </div>

      <div className="grid g-main">
        <Card title="Recent events" actions={<Link to="events">Explore →</Link>}>
          <Load state={recent} isEmpty={(d) => d.items.length === 0} empty="No events ingested yet.">
            {(d) => <EventTable rows={d.items} compact />}
          </Load>
        </Card>
        <Card title="Recent learning activity" actions={<Link to="learning">Learning →</Link>}>
          <Load state={learning} isEmpty={(d) => d.items.length === 0} empty="No learning sessions yet.">
            {(d) => (
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {d.items.map((l) => (
                  <li key={l.id} style={{ padding: "9px 0", borderBottom: "1px solid var(--border-soft)" }}>
                    <div className="spread">
                      <Link to="learning" param={l.id} className="mono">{l.source_key}</Link>
                      <Badge value={l.status} />
                    </div>
                    <div className="small muted">
                      v{l.source_adapter_version}{l.target_version ? ` → v${l.target_version}` : ""} · {l.learning_modes.join(", ") || "—"} · {fmtTime(l.created_at)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Load>
        </Card>
      </div>
    </>
  );
}
