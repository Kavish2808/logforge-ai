// Adapter Evolution — versions + a chronological timeline from /views/sources/{key}/timeline.
import { useState } from "react";
import { getSource, getSources, getTimeline, learningAction, rollbackOnboardedAdapter } from "../api/endpoints";
import type { SourceDetail } from "../api/types";
import { Badge, Card, KV, Load } from "../components/ui";
import { fmtTime } from "../lib/format";
import { Link } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

// Timeline detail values: readable timestamps and rates; objects stay inspectable as JSON.
function detailValue(key: string, v: unknown) {
  if (typeof v === "object") return <code style={{ overflowWrap: "anywhere" }}>{JSON.stringify(v)}</code>;
  if (typeof v === "number" && key.endsWith("rate") && v >= 0 && v <= 1) return `${(v * 100).toFixed(0)}%`;
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtTime(v);
  return String(v);
}

function refLinks(refs: Record<string, unknown>) {
  const out = [];
  if (refs.event_id) out.push(<Link key="e" to="events" param={String(refs.event_id)}>evidence event</Link>);
  if (refs.learning_session_id) out.push(<Link key="l" to="learning" param={String(refs.learning_session_id)}>learning session</Link>);
  if (refs.onboarding_session_id) out.push(<Link key="o" to="onboarding" param={String(refs.onboarding_session_id)}>onboarding session</Link>);
  if (refs.trigger_event_id) out.push(<Link key="t" to="events" param={String(refs.trigger_event_id)}>trigger event</Link>);
  if (refs.version) out.push(<span key="v" className="chip">v{String(refs.version)}</span>);
  return out.length ? <div className="row small" style={{ marginTop: 4 }}>{out}</div> : null;
}

function Versions({ source, onChanged }: { source: SourceDetail; onChanged: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const active = source.versions.find((v) => v.status === "ACTIVE");

  const rollback = async () => {
    if (!active) return;
    setBusy(true);
    setMsg(null);
    try {
      // Roll back through the workflow that created the version, so its session record stays consistent.
      if (active.origin === "phase6_learning") await learningAction(active.session_id, "rollback", { reason: "rolled back from Adapter Evolution" });
      else await rollbackOnboardedAdapter(source.source_key, "rolled back from Adapter Evolution");
      setMsg({ kind: "ok", text: `v${active.version} rolled back.` });
      setConfirm(false);
      onChanged();
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  if (!source.versions.length) {
    return <div className="faint">Shipped YAML adapter — its versions live in the repository; the timeline shows its Phase 5 baseline history.</div>;
  }
  return (
    <>
      <div className="versions" aria-label="Adapter versions">
        {source.versions.map((v, i) => (
          <span key={v.version} className="row" style={{ gap: 6 }}>
            <div className={`version${v.status === "ACTIVE" ? " active" : ""}`}>
              <div className="spread"><strong className="mono">v{v.version}</strong><Badge value={v.status} /></div>
              <div className="small muted">{v.origin === "phase6_learning" ? "learned (Phase 6)" : "onboarded (Phase 3)"}</div>
              <div className="small faint">{fmtTime(v.approved_at)}</div>
            </div>
            {i < source.versions.length - 1 && <span className="arrow" aria-hidden="true">→</span>}
          </span>
        ))}
      </div>
      {active && (
        <div style={{ marginTop: 12 }}>
          {!confirm ? (
            <button className="danger" onClick={() => setConfirm(true)} disabled={busy}>Roll back v{active.version}…</button>
          ) : (
            <div className="boundary row">
              <span>Withdraw v{active.version}? The previous version (if any) becomes active again; no version or history is deleted.</span>
              <button className="primary" onClick={rollback} disabled={busy}>Confirm rollback</button>
              <button className="ghost" onClick={() => setConfirm(false)}>Cancel</button>
            </div>
          )}
        </div>
      )}
      {msg && <div className={`notice ${msg.kind}`} style={{ marginTop: 10 }} role="status">{msg.text}</div>}
    </>
  );
}

function Picker() {
  const sources = useApi((s) => getSources(s), []);
  return (
    <>
      <div className="page-head"><div><div className="eyebrow">Adapt · versioned parsers</div><h1>Adapter Evolution</h1><p>How each source's adapter and baseline changed over time — and why.</p></div></div>
      <Card title="Choose a source">
        <Load state={sources} isEmpty={(d) => d.items.length === 0} empty="No sources yet.">
          {(d) => (
            <div className="grid g3">
              {[...d.items].sort((a, b) => Number(b.kind === "onboarded") - Number(a.kind === "onboarded")).map((s) => (
                <Link key={s.source_key} to="evolution" param={s.source_key} className="card">
                  <div className="spread"><strong className="mono">{s.source_key}</strong><span className="small muted">{s.kind.replace("_", " ")}</span></div>
                  <div className="small muted">
                    {s.active_version ? `active v${s.active_version}` : "no DB versions"} · baseline {s.baseline ? `v${s.baseline.version}` : "—"} ·{" "}
                    {Object.values(s.learning_sessions).reduce((a, b) => a + b, 0)} learning session(s)
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Load>
      </Card>
    </>
  );
}

export function AdapterEvolution({ sourceKey }: { sourceKey: string | null }) {
  if (!sourceKey) return <Picker />;
  return <Evolution sourceKey={sourceKey} />;
}

function Evolution({ sourceKey }: { sourceKey: string }) {
  const source = useApi((s) => getSource(sourceKey, s), [sourceKey]);
  const timeline = useApi((s) => getTimeline(sourceKey, s), [sourceKey]);
  const reload = () => { source.reload(); timeline.reload(); };
  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="evolution">Adapter Evolution</Link> / {sourceKey}</div>
          <h1 className="mono">{sourceKey}</h1>
        </div>
        <Link to="sources" param={sourceKey}>Source intelligence →</Link>
      </div>
      <Card title="Versions">
        <Load state={source}>{(s) => <Versions source={s} onChanged={reload} />}</Load>
      </Card>
      <Card title="Timeline">
        <Load state={timeline} isEmpty={(t) => t.entries.length === 0} empty="No recorded history for this source yet.">
          {(t) => (
            <ol className="timeline">
              {t.entries.map((e, i) => (
                <li key={i} className={`p-${e.phase}`}>
                  <div className="spread">
                    <strong>{e.title}</strong>
                    <span className="row"><span className="badge">{e.phase}</span><span className="small faint">{fmtTime(e.at)}</span></span>
                  </div>
                  <div className="small mono faint">{e.kind}</div>
                  {Object.keys(e.details).length > 0 && (
                    <div className="small" style={{ marginTop: 4 }}>
                      <KV items={Object.entries(e.details).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => [
                        k.replace(/_/g, " "),
                        detailValue(k, v),
                      ])} />
                    </div>
                  )}
                  {refLinks(e.refs)}
                </li>
              ))}
            </ol>
          )}
        </Load>
      </Card>
    </>
  );
}
