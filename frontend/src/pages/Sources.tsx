// Source intelligence from /views/sources and /views/sources/{key}.
import { getSource, getSources } from "../api/endpoints";
import type { SourceSummary } from "../api/types";
import { EventTable } from "../components/EventTable";
import { Badge, Card, KV, Load, Stat } from "../components/ui";
import { fmtAgo, fmtTime, num } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { useApi } from "../lib/useApi";

const driftCount = (s: SourceSummary) => (s.drift.DRIFT ?? 0) + (s.drift.POSSIBLE_FORMAT_DRIFT ?? 0);
const entries = (o: Record<string, number>) => Object.entries(o).map(([k, v]) => `${k} (${v})`).join(", ") || "—";

function SourceList() {
  const sources = useApi((s) => getSources(s), []);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sources</h1>
          <p>Every source LogForge knows: shipped vendor and generic adapters, and sources learned through onboarding.</p>
        </div>
      </div>
      <Card>
        <Load state={sources} isEmpty={(d) => d.items.length === 0} empty="No sources yet — ingest a log or onboard a new vendor.">
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Source</th><th>Kind</th><th>Events</th><th>Partial rate</th><th>Formats</th><th>Versions seen</th>
                    <th>Active</th><th>Baseline</th><th>Drift</th><th>Last seen</th></tr>
                </thead>
                <tbody>
                  {d.items.map((s) => (
                    <tr key={s.source_key} className="clickable" tabIndex={0} aria-label={`Open source ${s.source_key}`}
                      onClick={() => navigate("sources", s.source_key)}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate("sources", s.source_key); }}>
                      <td className="mono">{s.source_key}<div className="small muted">{s.vendor ?? ""}{s.product ? ` / ${s.product}` : ""}</div></td>
                      <td className="small">{s.kind.replace("_", " ")}</td>
                      <td>{num(s.events.total)}</td>
                      <td>{s.partial_rate === null ? "—" : `${(s.partial_rate * 100).toFixed(1)}%`}</td>
                      <td className="small">{entries(s.formats)}</td>
                      <td className="small mono">{Object.keys(s.adapter_versions_seen).filter((v) => v !== "NONE").map((v) => `v${v}`).join(" ") || "—"}</td>
                      <td className="mono">{s.active_version ? `v${s.active_version}` : "—"}</td>
                      <td className="small">{s.baseline ? `v${s.baseline.version} · ${s.baseline.origin}` : "—"}</td>
                      <td>{driftCount(s) ? <span className="badge b-warn">{driftCount(s)}</span> : <span className="faint">0</span>}
                        {s.under_review ? <> <span className="badge b-review">{s.under_review} pending</span></> : null}</td>
                      <td className="small">{fmtAgo(s.last_seen)}</td>
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

function SourceDetailView({ sourceKey }: { sourceKey: string }) {
  const source = useApi((s) => getSource(sourceKey, s), [sourceKey]);
  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="sources">Sources</Link> / {sourceKey}</div>
          <h1 className="mono">{sourceKey}</h1>
        </div>
        <Link to="evolution" param={sourceKey}>Adapter evolution timeline →</Link>
      </div>
      <Load state={source}>
        {(s) => (
          <>
            <div className="stats">
              <Stat accent label="Kind" value={s.kind.replace("_", " ")} hint={`${s.vendor ?? "—"} / ${s.product ?? "—"}`} />
              <Stat label="Events" value={num(s.events.total)} hint={Object.entries(s.events).filter(([k]) => k !== "total").map(([k, v]) => `${v} ${k}`).join(" · ") || "—"} />
              <Stat label="Partial rate" value={s.partial_rate === null ? "—" : `${(s.partial_rate * 100).toFixed(1)}%`} hint="of this source's parsed events" />
              <Stat label="Active version" value={s.active_version ? `v${s.active_version}` : "—"} />
              <Stat label="Drift events" value={num(driftCount(s))} hint={`${s.under_review} awaiting review`} />
              <Stat label="Last seen" value={fmtAgo(s.last_seen)} hint={fmtTime(s.last_seen)} />
            </div>
            <div className="grid g2" style={{ marginTop: 16 }}>
              <Card title="Baseline (Phase 5)">
                {s.baseline ? (
                  <KV items={[
                    ["Version", `v${s.baseline.version as number}`], ["Origin", String(s.baseline.origin)],
                    ["Reference fields", String(s.baseline.reference_field_count ?? "—")],
                    ["Approved variants", String(s.baseline.accepted_variants ?? 0)],
                    ["Adapter version", String(s.baseline.adapter_version ?? "—")], ["Updated", fmtTime(s.baseline.updated_at as string)],
                  ]} />
                ) : <div className="faint">No baseline yet (created by the first event of a known vendor source).</div>}
                {s.baseline_history.length > 0 && (
                  <>
                    <h3 style={{ marginTop: 14 }}>Baseline history</h3>
                    <table>
                      <thead><tr><th>v</th><th>Action</th><th>Change</th><th>When</th></tr></thead>
                      <tbody>
                        {s.baseline_history.map((h, i) => {
                          const c = (h.changes ?? {}) as { added_fields?: string[]; removed_fields?: string[] };
                          return (
                            <tr key={i}>
                              <td>v{String(h.version)}</td><td><span className="chip">{String(h.action)}</span></td>
                              <td className="small">{[...(c.added_fields ?? []).map((f) => `+${f}`), ...(c.removed_fields ?? []).map((f) => `−${f}`)].join(" ") || "—"}</td>
                              <td className="small">{fmtTime(h.created_at as string)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </>
                )}
              </Card>
              <Card title="Adapter versions">
                {s.versions.length === 0 ? (
                  <div className="faint">{s.kind.startsWith("shipped") ? "Shipped YAML adapter — versioned in the repository, not in the database." : "No versions."}</div>
                ) : (
                  <table>
                    <thead><tr><th>Version</th><th>Status</th><th>Origin</th><th>Approved</th><th>Match rate</th></tr></thead>
                    <tbody>
                      {s.versions.map((v) => (
                        <tr key={v.version}>
                          <td className="mono">v{v.version}</td><td><Badge value={v.status} /></td>
                          <td className="small">{v.origin.replace("_", " ")}</td>
                          <td className="small">{v.approved_by ?? "—"} · {fmtTime(v.approved_at)}</td>
                          <td>{v.match_rate === null ? "—" : `${(v.match_rate * 100).toFixed(0)}%`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <h3 style={{ marginTop: 14 }}>Observed</h3>
                <KV items={[["Formats", entries(s.formats)], ["Versions seen", entries(s.adapter_versions_seen)],
                  ["Learning sessions", entries(s.learning_sessions)]]} />
              </Card>
            </div>
            <Card title="Recent drift">
              {s.recent_drift.length === 0 ? <div className="faint">No drift recorded for this source.</div> : <EventTable rows={s.recent_drift} compact />}
            </Card>
          </>
        )}
      </Load>
    </>
  );
}

export function Sources({ sourceKey }: { sourceKey: string | null }) {
  return sourceKey ? <SourceDetailView sourceKey={sourceKey} /> : <SourceList />;
}
