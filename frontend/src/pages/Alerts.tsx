// Alerts — the internal alert bus: severity, read/unread, acknowledgement and delivery outcomes.
import { useState } from "react";
import { AlertOctagon, AlertTriangle, BellRing, Info, ShieldAlert } from "lucide-react";
import { ackAlert, getAlertChannels, getAlerts, markAlertRead, sweepAlerts } from "../api/endpoints";
import type { Alert } from "../api/types";
import { Badge, Card, Json, Load, Stat } from "../components/ui";
import { EmptyState } from "../components/ui";
import { fmtAgo, fmtTime, num } from "../lib/format";
import { errorMessage, useApi } from "../lib/useApi";

const SEVERITY_CLASS: Record<string, string> = { CRITICAL: "b-fail", HIGH: "b-warn", MEDIUM: "b-warn", LOW: "b-info", INFO: "b-neutral" };
const SEVERITY_ICON = { CRITICAL: AlertOctagon, HIGH: ShieldAlert, MEDIUM: AlertTriangle, LOW: Info, INFO: Info } as const;
const SEVERITY_LEVEL: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0 };

function AlertItem({ a, onChange }: { a: Alert; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); onChange(); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  };
  const Icon = SEVERITY_ICON[a.severity as keyof typeof SEVERITY_ICON] ?? BellRing;
  const level = SEVERITY_LEVEL[a.severity] ?? 0;
  return (
    <li className={`alert-item sev-${a.severity.toLowerCase()}${a.read ? "" : " unread"}`}>
      <span className="alert-icon" aria-hidden="true"><Icon size={17} /></span>
      <div className="alert-body">
      <div className="spread">
        <div className="row">
          <span className={`badge ${SEVERITY_CLASS[a.severity] ?? ""}`} title={`severity ${level + 1}/5`}>
            <span className="sev-bars" aria-hidden="true">{[1, 2, 3, 4].map((n) => <i key={n} style={n <= level ? { background: "currentColor" } : undefined} />)}</span>
            {a.severity}</span>
          <strong className="alert-title">{a.title}</strong>
          {!a.read && <span className="badge b-info">unread</span>}
          {a.occurrences > 1 && <span className="small muted">×{a.occurrences}</span>}
        </div>
        <div className="row">
          <Badge value={a.status} />
          <span className="small muted" title={fmtTime(a.last_seen_at)}>{fmtAgo(a.last_seen_at)}</span>
        </div>
      </div>
      <div className="small muted" style={{ margin: "4px 0" }}>{a.message}</div>
      {a.object_type && <div className="small faint" style={{ marginBottom: 4 }}>source: <span className="mono">{a.object_type}{a.object_id ? ` · ${a.object_id}` : ""}</span> · first seen {fmtTime(a.created_at)}</div>}
      <div className="row">
        <span className="chip">{a.kind.replace(/_/g, " ")}</span>
        {a.deliveries.map((d, i) => <span key={i} className={`badge ${d.ok ? "b-ok" : "b-fail"}`} title={d.error}>{d.channel}{d.ok ? "" : " failed"}</span>)}
        <button className="ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Details"}</button>
        <button className="ghost" disabled={busy} onClick={() => act(() => markAlertRead(a.id, a.read))}>{a.read ? "Mark unread" : "Mark read"}</button>
        {a.status === "OPEN" && <button disabled={busy} onClick={() => act(() => ackAlert(a.id, null))}>Acknowledge</button>}
        {a.acknowledged_by && <span className="small muted">acknowledged by {a.acknowledged_by} {fmtAgo(a.acknowledged_at)}</span>}
      </div>
      {error && <div className="notice fail" role="alert" style={{ marginTop: 6 }}>{error}</div>}
      {open && <Json value={a.details} />}
      </div>
    </li>
  );
}

export function AlertsPage() {
  const [status, setStatus] = useState<string>("OPEN");
  const alerts = useApi((s) => getAlerts({ status: status || undefined, limit: 100 }, s), [status]);
  const channels = useApi((s) => getAlertChannels(s), []);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Alert center</div>
          <h1>Alerts</h1>
          <p>Critical drift, overdue and escalated reviews, learning regressions, integrity and vault failures, overflow and parser-failure spikes.</p>
        </div>
        <button onClick={async () => {
          try { await sweepAlerts(); setMsg({ kind: "ok", text: "Checks ran; alerts are up to date." }); alerts.reload(); }
          catch (err) { setMsg({ kind: "fail", text: errorMessage(err) }); }
        }}>Run checks now</button>
      </div>
      {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
      <Load state={alerts}>
        {(d) => (
          <div className="stats">
            <Stat accent label="Open" value={num(d.counts.by_status.OPEN ?? 0)} />
            <Stat label="Unread" value={num(d.counts.unread)} />
            {Object.entries(d.counts.open_by_severity).map(([k, v]) => <Stat key={k} label={`Open ${k}`} value={num(v)} />)}
          </div>
        )}
      </Load>
      <div className="grid g-main" style={{ marginTop: 16 }}>
        <Card title="Alerts" actions={
          <label className="field">Status
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Alert status">
              <option value="OPEN">Open</option><option value="ACKNOWLEDGED">Acknowledged</option><option value="">All</option>
            </select>
          </label>}>
          <Load state={alerts} isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState icon={BellRing} title="No alerts">No alert matches this filter. Checks run on the scheduler; “Run checks now” evaluates them immediately.</EmptyState>}>
            {(d) => <ul className="alert-list">{d.items.map((a) => <AlertItem key={a.id} a={a} onChange={alerts.reload} />)}</ul>}
          </Load>
        </Card>
        <Card title="Delivery channels">
          <Load state={channels}>
            {(c) => (
              <>
                <table>
                  <tbody>{c.channels.map((ch) => (
                    <tr key={ch.channel}><td>{ch.channel}</td><td>{ch.configured ? <span className="badge b-ok">configured</span> : <span className="badge b-neutral">not configured</span>}</td></tr>
                  ))}</tbody>
                </table>
                <p className="small muted">Internal delivery is always on. Webhook, Slack, Teams and email adapters activate only when configured; destinations are never displayed.</p>
              </>
            )}
          </Load>
        </Card>
      </div>
    </>
  );
}
