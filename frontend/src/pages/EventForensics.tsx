// Event Forensics — the signature LogForge screen. Everything comes from
// /views/events/{id}/lineage (+ the stored event), nothing is inferred here.
import { getEvent, getEvents, getLineage } from "../api/endpoints";
import type { AccountedField, Lineage, LineageStage, UniversalEvent } from "../api/types";
import { Badge, Card, Id, Json, KV, Load, Tabs } from "../components/ui";
import { fmtTime, num } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";

const ICON: Record<string, string> = { OK: "✓", WARN: "!", FAIL: "✕", SKIPPED: "–" };
const STAGE_LABEL: Record<string, string> = {
  RAW: "Raw event", FORMAT_DETECTION: "Format detection", PARSER: "Parser", ADAPTER: "Adapter @ version",
  NORMALIZATION: "Normalization", FIELD_ACCOUNTING: "Field accounting", WARNINGS: "Warnings",
  DRIFT_DECISION: "Drift decision", BASELINE: "Baseline", LEARNING_HISTORY: "Learning history",
};
// A few meaningful details per stage (the full details object stays inspectable).
const STAGE_KEYS: Record<string, string[]> = {
  RAW: ["received_at", "raw_hash"],
  FORMAT_DETECTION: ["format", "via"],
  PARSER: ["parser", "pipeline_version", "error"],
  ADAPTER: ["origin", "version_status_now", "active_version_now", "approved_by", "approved_at", "session_id", "note"],
  NORMALIZATION: ["ocsf_class", "event_type", "event_action", "severity", "event_timestamp"],
  DRIFT_DECISION: ["source_key", "severity", "change_types", "matched", "baseline_version"],
  BASELINE: ["baseline_version", "origin", "relation", "accepted_variants"],
};

function stageTimestamp(s: LineageStage): string | null {
  const d = s.details as Record<string, unknown>;
  if (s.stage === "RAW") return (d.received_at as string) ?? null;
  if (s.stage === "DRIFT_DECISION") return ((d.review as Record<string, string> | null)?.reviewed_at) ?? null;
  if (s.stage === "ADAPTER") return (d.approved_at as string) ?? null;
  return null;
}

function renderValue(v: unknown) {
  if (v === null || v === undefined) return null;
  if (Array.isArray(v)) return v.length ? v.map((x) => <span key={String(x)} className="chip" style={{ marginRight: 4 }}>{String(x)}</span>) : null;
  if (typeof v === "object") return <code>{JSON.stringify(v)}</code>;
  const s = String(v);
  return /^\d{4}-\d{2}-\d{2}T/.test(s) ? fmtTime(s) : s.length > 40 && /^[0-9a-f]+$/.test(s) ? <code>{s}</code> : s;
}

function Flow({ lineage }: { lineage: Lineage }) {
  return (
    <ol className="flow" aria-label="Processing lineage" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {lineage.chain.map((s, i) => {
        const ts = stageTimestamp(s);
        const keys = STAGE_KEYS[s.stage] ?? [];
        const shown = keys.map((k) => [k, (s.details as Record<string, unknown>)[k]] as [string, unknown]).filter(([, v]) => v !== null && v !== undefined && v !== "");
        return (
          <li className="flow-step" key={s.stage}>
            <div className="flow-rail" aria-hidden="true">
              <div className={`flow-node n-${s.outcome}`}>{ICON[s.outcome] ?? "?"}</div>
              {i < lineage.chain.length - 1 && <div className="flow-line" />}
            </div>
            <div className="flow-body">
              <div className="spread">
                <strong>{STAGE_LABEL[s.stage] ?? s.stage}</strong>
                <span className="row">{ts && <span className="small faint">{fmtTime(ts)}</span>}<Badge value={s.outcome} /></span>
              </div>
              <div className="muted" style={{ marginTop: 2 }}>{s.summary}</div>
              {shown.length > 0 && (
                <div className="small" style={{ marginTop: 6 }}>
                  <KV items={shown.map(([k, v]) => [k.replace(/_/g, " "), renderValue(v)])} />
                </div>
              )}
              {s.stage === "LEARNING_HISTORY" && <LearningRefs details={s.details} />}
              {Object.keys(s.details).length > 0 && (
                <details className="small" style={{ marginTop: 6 }}>
                  <summary className="faint">All recorded details</summary>
                  <Json value={s.details} />
                </details>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function LearningRefs({ details }: { details: Record<string, unknown> }) {
  const learning = (details.learning_sessions as { learning_session_id: string; role: string; status: string }[]) ?? [];
  const onboarding = (details.onboarding_sessions as { onboarding_session_id: string; status: string }[]) ?? [];
  if (!learning.length && !onboarding.length) return null;
  return (
    <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
      {learning.map((l) => <li key={l.learning_session_id}>Learning session <Link to="learning" param={l.learning_session_id}>{l.learning_session_id.slice(-8)}</Link> — {l.role}, <Badge value={l.status} /></li>)}
      {onboarding.map((o) => <li key={o.onboarding_session_id}>Onboarding sample in <Link to="onboarding" param={o.onboarding_session_id}>{o.onboarding_session_id.slice(-8)}</Link> — <Badge value={o.status} /></li>)}
    </ul>
  );
}

function Verdict({ lineage }: { lineage: Lineage }) {
  const ok = lineage.nothing_silently_discarded;
  return (
    <div className={`verdict ${ok ? "ok" : "fail"}`} role="status">
      <span style={{ fontSize: 22 }} aria-hidden="true">{ok ? "✓" : "✕"}</span>
      <div>
        <div>{ok ? "NOTHING SILENTLY DISCARDED" : lineage.integrity.verified ? "UNACCOUNTED FIELDS DETECTED" : "INTEGRITY CHECK FAILED"}</div>
        <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18, fontWeight: 400 }}>
          {lineage.basis.map((b) => <li key={b}>{b}</li>)}
        </ul>
      </div>
    </div>
  );
}

function Strip({ lineage }: { lineage: Lineage }) {
  const fa = lineage.field_accounting;
  const failed = lineage.status === "FAILED";
  const steps: [string, string, string][] = [
    ["RAW", `${num(lineage.integrity.raw_bytes)} bytes`, "OK"],
    ["PARSED", failed ? "not parsed" : `${fa.parsed_count} fields`, failed ? "FAIL" : "OK"],
    ["NORMALIZED", failed ? "—" : `${fa.mapped_count} mapped`, failed ? "SKIPPED" : lineage.status === "PARTIAL" ? "WARN" : "OK"],
    ["PRESERVED", failed ? "raw record" : `${fa.preserved_count} in extensions`, "OK"],
    ["VERIFIED", lineage.integrity.verified ? "SHA-256 match" : "hash mismatch", lineage.integrity.verified ? "OK" : "FAIL"],
  ];
  return (
    <div className="pipeline" style={{ margin: "14px 0" }} aria-label="Raw to verified">
      {steps.map(([name, detail, outcome], i) => (
        <span key={name} className="row" style={{ gap: 6 }}>
          <span className={`badge ${outcome === "OK" ? "b-ok" : outcome === "WARN" ? "b-warn" : outcome === "FAIL" ? "b-fail" : ""}`} style={{ padding: "5px 10px" }}>
            {name} <span style={{ fontWeight: 400 }}>· {detail}</span>
          </span>
          {i < steps.length - 1 && <span className="arrow" aria-hidden="true">↓</span>}
        </span>
      ))}
    </div>
  );
}

function Accounting({ lineage }: { lineage: Lineage }) {
  const fa = lineage.field_accounting;
  return (
    <>
      <div className="accounting">
        <div className="acc"><div className="small muted">Parsed</div><div className="value">{fa.parsed_count}</div></div>
        <div className="acc"><div className="small muted">Mapped</div><div className="value" style={{ color: "var(--ok)" }}>{fa.mapped_count}</div></div>
        <div className="acc"><div className="small muted">Preserved</div><div className="value" style={{ color: "var(--info)" }}>{fa.preserved_count}</div></div>
        <div className="acc" style={fa.unaccounted.length ? { borderColor: "var(--fail)", background: "var(--fail-bg)" } : undefined}>
          <div className="small muted">Unaccounted</div>
          <div className="value" style={{ color: fa.unaccounted.length ? "var(--fail)" : "var(--muted)" }}>{fa.unaccounted.length}</div>
        </div>
      </div>
      {fa.fields.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 12, maxHeight: 360, overflowY: "auto" }}>
          <table>
            <thead><tr><th>Parsed field</th><th>Outcome</th><th>Where it went</th></tr></thead>
            <tbody>{fa.fields.map((f) => <FieldRow key={f.field} f={f} />)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

function FieldRow({ f }: { f: AccountedField }) {
  if (f.outcome === "PRESERVED") {
    return (
      <tr>
        <td className="mono">{f.field}</td>
        <td><span className="badge b-info">PRESERVED</span></td>
        <td><div className="mono small wrap-id"><Id value={f.location ?? ""} /></div><div className="mono small muted" style={{ overflowWrap: "anywhere" }}>{JSON.stringify(f.value)}</div></td>
      </tr>
    );
  }
  if (f.outcome === "MAPPED") {
    return (
      <tr>
        <td className="mono">{f.field}</td>
        <td><span className="badge b-ok">MAPPED</span></td>
        <td><div className="mono small wrap-id"><Id value={f.target ?? ""} /></div><div className="small muted">{f.normalized_value_present ? "normalized value present" : <span className="badge b-warn">no normalized value (see warnings)</span>}</div></td>
      </tr>
    );
  }
  return (
    <tr>
      <td className="mono">{f.field}</td>
      <td><span className="badge b-fail">UNACCOUNTED</span></td>
      <td className="small">Neither mapped nor preserved</td>
    </tr>
  );
}

function RawNormalizedTabs({ lineage, event, eventError }: { lineage: Lineage; event: UniversalEvent | null; eventError: string | null }) {
  const warnings = (lineage.chain.find((s) => s.stage === "WARNINGS")?.details.warnings as { kind: string; message: string }[]) ?? [];
  const unavailable = <div className="notice warn">The stored event could not be loaded through /events ({eventError ?? "unavailable"}). Lineage and integrity above are still valid.</div>;
  const typeMismatch = lineage.field_accounting.fields.filter((f) => f.outcome === "PRESERVED" && warnings.some((w) => w.kind === "TYPE_MISMATCH_PRESERVED" && w.message.startsWith(`${f.field}:`)));
  return (
    <Tabs tabs={[
      { id: "raw", label: "Raw event", content: event ? <pre className="code">{event.raw_event}</pre> : unavailable },
      { id: "norm", label: "Normalized", content: event ? (
        <div className="grid g2">
          <div><h3>Network / user / process</h3><Json value={{ network: event.network, user: event.user, process: event.process }} /></div>
          <div><h3>normalized_event</h3><Json value={event.normalized_event} /></div>
        </div>) : unavailable },
      { id: "ext", label: `Extensions (${lineage.field_accounting.preserved_count})`, content: event ? (
        Object.keys(event.extensions ?? {}).length === 0 ? <div className="faint">No fields were preserved in extensions.</div> : (
          <table>
            <thead><tr><th>Field</th><th>Preserved value</th></tr></thead>
            <tbody>{Object.entries(event.extensions).map(([k, v]) => (
              <tr key={k}><td className="mono">extensions.{k}</td><td className="mono small">{JSON.stringify(v)}</td></tr>))}</tbody>
          </table>)) : unavailable },
      { id: "warn", label: `Warnings (${warnings.length})`, content: warnings.length === 0 ? <div className="faint">No warnings.</div> : (
        <>
          {typeMismatch.map((f) => (
            <div key={f.field} className="notice warn" style={{ marginBottom: 8 }}>
              <strong className="mono">{f.field} = {JSON.stringify(f.value)}</strong> — TYPE_MISMATCH: not converted, not mapped.
              Preserved verbatim as <span className="mono">{f.location}</span>.
            </div>
          ))}
          <table>
            <thead><tr><th>Kind</th><th>Message</th></tr></thead>
            <tbody>{warnings.map((w, i) => <tr key={i}><td><span className="badge b-warn">{w.kind}</span></td><td className="small">{w.message}</td></tr>)}</tbody>
          </table>
        </>) },
      { id: "hash", label: "Hash / integrity", content: (
        <KV items={[
          ["Algorithm", lineage.integrity.algorithm],
          ["Stored hash", <code key="s">{lineage.integrity.stored}</code>],
          ["Recomputed now", <code key="r">{lineage.integrity.recomputed}</code>],
          ["Result", lineage.integrity.verified ? <span className="badge b-ok">VERIFIED — identical</span> : <span className="badge b-fail">MISMATCH</span>],
          ["Raw size", `${num(lineage.integrity.raw_bytes)} bytes`],
          ["Event id", <span key="e"><code>{lineage.event_id}</code> <span className="faint small">(ULID: unique, time-ordered; not derived from content — the SHA-256 identifies content)</span></span>],
        ]} />) },
    ]} />
  );
}

export function EventForensics({ id }: { id: string }) {
  const lineage = useApi((s) => getLineage(id, s), [id]);
  const row = useApi((s) => getEvents({ search: id, limit: 1 }, s), [id]);
  const event = useApi((s) => getEvent(id, s), [id]);
  const r = row.data?.items[0];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="events">Event Explorer</Link> / Forensics</div>
          <h1 className="row">Event forensics <code style={{ fontSize: 13 }}>{id}</code> {r && <Badge value={r.status} />}</h1>
        </div>
      </div>
      <Load state={lineage}>
        {(l) => (
          <>
            <Verdict lineage={l} />
            <Strip lineage={l} />
            <div className="grid g-main">
              <Card title="Processing lineage"><Flow lineage={l} /></Card>
              <div>
                <Card title="Event">
                  {r ? (
                    <KV items={[
                      ["Status", <Badge key="s" value={r.status} />], ["Format", <span key="f" className="chip">{r.format_detected}</span>],
                      ["Source", r.source_key ? <Link key="src" to="sources" param={r.source_key} className="mono">{r.source_key}</Link> : null],
                      ["Vendor", r.vendor], ["Product", r.product],
                      ["Adapter", r.adapter_id ? <span key="a" className="mono">{r.adapter_id}</span> : null],
                      ["Adapter version", r.adapter_version ? `v${r.adapter_version} (${r.adapter_source === "onboarded" ? "onboarded" : "shipped YAML"})` : null],
                      ["OCSF class", r.ocsf_class_name], ["Received", fmtTime(r.received_at)],
                      ["Raw SHA-256", <code key="h" style={{ fontSize: 11 }}>{r.raw_hash}</code>],
                    ]} />
                  ) : <div className="faint">Loading event…</div>}
                </Card>
                <Card title="Field accounting"><Accounting lineage={l} /></Card>
              </div>
            </div>
            <Card title="Raw vs normalized" className="" >
              <RawNormalizedTabs lineage={l} event={event.data} eventError={event.error} />
            </Card>
          </>
        )}
      </Load>
    </>
  );
}
