import { useState } from "react";
import { getEvents, getSources, getSummary, getTrust, listLearning } from "../api/endpoints";
import { EventTable } from "../components/EventTable";
import { Quiet } from "../components/trust";
import { Badge, Bars, Card, Load, Stat, STATUS_COLORS, TrendChart } from "../components/ui";
import { fmtAgo, fmtTime, num, pct, sum } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";

export function Overview() {
  const [bucket, setBucket] = useState<"hour" | "day">("hour");
  const summary = useApi((s) => getSummary({ bucket }, s), [bucket]);
  const recent = useApi((s) => getEvents({ limit: 8 }, s), []);
  const queue = useApi((s) => getEvents({ status: "UNDER_REVIEW", limit: 5 }, s), []);
  const sources = useApi((s) => getSources(s), []);
  const learning = useApi((s) => listLearning({ limit: 5 }, s), []);
  const trust = useApi((s) => getTrust(s), []);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Live state of ingestion, drift detection and adaptive learning — every number is read from the database.</p>
        </div>
        {recent.data?.items[0] && (
          <span className="small muted">Last event received {fmtAgo(recent.data.items[0].received_at)}</span>
        )}
      </div>

      <Load state={summary}>
        {(s) => {
          const total = s.totals.events;
          const st = (k: string) => s.by_status[k] ?? 0;
          const adapters = sum(s.adapters);
          return (
            <>
              <div className="stats">
                <Stat accent label="Total events" value={num(total)} />
                <Stat label="SUCCESS" value={num(st("SUCCESS"))} hint={`${pct(st("SUCCESS"), total)} of ${num(total)}`} />
                <Stat label="PARTIAL" value={num(st("PARTIAL"))} hint={`${pct(st("PARTIAL"), total)} — values preserved`} />
                <Stat label="FAILED" value={num(st("FAILED"))} hint={`${pct(st("FAILED"), total)} — raw preserved`} />
                <Stat label="UNDER REVIEW" value={num(st("UNDER_REVIEW"))} hint="awaiting a human decision" />
                <Stat label="Unique sources" value={num(s.totals.unique_sources)} />
                <Stat label="Unique vendors" value={num(s.totals.unique_vendors)} />
                <Stat label="Drift events" value={num(s.totals.drift_events)} hint="DRIFT + possible format drift" />
                <Stat label="Active adapters" value={num(adapters)}
                  hint={`${s.adapters.shipped_vendor ?? 0} vendor · ${s.adapters.shipped_generic ?? 0} generic · ${s.adapters.onboarded_active ?? 0} onboarded`} />
                <Stat label="Learning sessions" value={num(sum(s.learning_sessions))}
                  hint={Object.entries(s.learning_sessions).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "none yet"} />
              </div>

              <div className="grid g-main" style={{ marginTop: 16 }}>
                <Card title="Events over time" actions={
                  <div className="row" role="group" aria-label="Trend bucket">
                    {(["hour", "day"] as const).map((b) => (
                      <button key={b} className={bucket === b ? "primary" : ""} onClick={() => setBucket(b)}>{b}</button>
                    ))}
                  </div>}>
                  <TrendChart buckets={s.trend} bucket={bucket} />
                  <div className="row small" style={{ marginTop: 8 }}>
                    {Object.entries(STATUS_COLORS).map(([k, c]) => (
                      <span key={k} className="row" style={{ gap: 4 }}><span className="dot" style={{ background: c }} />{k.replace("_", " ")}</span>
                    ))}
                  </div>
                </Card>
                <Card title="Status distribution">
                  <Bars data={s.by_status} colorFor={(k) => STATUS_COLORS[k] ?? "#3b5bdb"} />
                  <h3 style={{ marginTop: 16 }}>Drift decisions</h3>
                  <Bars data={s.by_drift_status} />
                </Card>
              </div>
            </>
          );
        }}
      </Load>

      <div className="grid g2" style={{ marginTop: 16 }}>
        <Card title="Drift queue" actions={<Link to="drift">Open queue →</Link>}>
          <Load state={queue} isEmpty={(d) => d.items.length === 0} empty="No events are awaiting review.">
            {(d) => <EventTable rows={d.items} compact />}
          </Load>
        </Card>
        <Card title="Top sources" actions={<Link to="sources">All sources →</Link>}>
          <Load state={sources} isEmpty={(d) => d.items.length === 0} empty="No sources yet.">
            {(d) => (
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
            )}
          </Load>
        </Card>
      </div>

      <Quiet state={trust}>
        {(t) => (
          <Card title="Trust & governance" actions={<Link to="integrity">Integrity →</Link>}>
            <div className="stats">
              <Stat label="Sealed (Merkle)" value={num(t.integrity.events_sealed)} hint={`${num(t.integrity.events_unsealed)} awaiting seal`} />
              <Stat label="Raw hot / cold" value={`${num(t.raw_vault.hot)} / ${num(t.raw_vault.cold_stored)}`} hint={`${num(t.raw_vault.cold_failed)} vault failure(s)`} />
              <Stat label="Spilled extensions" value={num(t.extension_overflow.events_spilled)} hint={`${num(t.extension_overflow.overflow_field_count)} fields in overflow`} />
              <Stat label="Open reviews" value={num(t.reviews.open)} hint={`${num((t.reviews.by_status.OVERDUE ?? 0) + (t.reviews.by_status.ESCALATED ?? 0))} overdue/escalated`} />
              <Stat label="Open alerts" value={num(t.alerts.by_status.OPEN ?? 0)} hint={`${num(t.alerts.unread)} unread`} />
              <Stat label="Audit records" value={num(t.audit.total)} hint={t.audit.head ? `head #${t.audit.head.seq}` : "none yet"} />
              <Stat label="Confidence ledger" value={num(t.confidence.total)} hint="suggestions with evidence" />
              <Stat label="Exports" value={num(t.exports.exports)} hint={`${num(t.exports.rows_exported)} rows`} />
            </div>
          </Card>
        )}
      </Quiet>

      <div className="grid g-main" style={{ marginTop: 16 }}>
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
                  <li key={l.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
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
