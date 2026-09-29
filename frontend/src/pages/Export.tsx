// Export — bounded, streaming NDJSON / JSON export under the published
// logforge.export.v1 contract. Selection reuses the Event Explorer filters.
import { useState } from "react";
import { exportEvents, getEvents, getExportLogs, getExportSchema } from "../api/endpoints";
import { EMPTY_FILTERS, EventFilterBar, EventFilterState, toQuery } from "../components/EventFilters";
import { Badge, Card, KV, Load, Stat } from "../components/ui";
import { fmtTime, num } from "../lib/format";
import { errorMessage, useApi } from "../lib/useApi";

const FORMATS = [
  { id: "ndjson", label: "NDJSON", hint: "One event record per line, then a trailer line (count, has_more, next_cursor)." },
  { id: "json", label: "JSON", hint: "One document with items[]; bounded to the JSON export limit." },
];

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ExportPage() {
  const [filters, setFilters] = useState<EventFilterState>(EMPTY_FILTERS);
  const [output, setOutput] = useState("ndjson");
  const [limit, setLimit] = useState("1000");
  const [includeRaw, setIncludeRaw] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const count = useApi((s) => getEvents({ ...toQuery(filters), limit: 1, include_total: true }, s), [JSON.stringify(filters)]);
  const schema = useApi((s) => getExportSchema(s), []);
  const logs = useApi((s) => getExportLogs(s), []);

  const run = async (from: string | null) => {
    setBusy(true);
    setMsg(null);
    try {
      const n = Number.parseInt(limit, 10);
      const { blob, headers } = await exportEvents({
        ...toQuery(filters), output, include_raw: includeRaw, limit: Number.isFinite(n) && n > 0 ? n : undefined, cursor: from ?? undefined,
      });
      const text = await blob.text();
      let next: string | null = null;
      let rows = 0;
      if (output === "ndjson") {
        const lines = text.trim().split("\n");
        const trailer = JSON.parse(lines[lines.length - 1] || "{}");
        next = trailer.has_more ? trailer.next_cursor : null;
        rows = trailer.count ?? lines.length - 1;
      } else {
        const doc = JSON.parse(text);
        next = doc.has_more ? doc.next_cursor : null;
        rows = doc.count ?? 0;
      }
      const cd = headers.get("content-disposition") ?? "";
      save(blob, /filename="([^"]+)"/.exec(cd)?.[1] ?? `logforge-export.${output}`);
      setCursor(next);
      setMsg({ kind: "ok", text: `Exported ${num(rows)} event(s)${next ? " — the limit was reached; continue with the next page." : "."}` });
      logs.reload();
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Data Egress</div>
          <h1>Export</h1>
          <p>Export normalized logs and cryptographic receipts for SIEM and lake ingestion.</p>
        </div>
      </div>
      <Card title="Selection">
        <EventFilterBar value={filters} onChange={(f) => { setFilters(f); setCursor(null); }} />
      </Card>
      <div className="grid g2">
        <Card title="Format">
          <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="small muted">Output format</legend>
            {FORMATS.map((f) => (
              <label key={f.id} className="row" style={{ padding: "4px 0" }}>
                <input type="radio" name="export-format" value={f.id} checked={output === f.id} onChange={() => { setOutput(f.id); setCursor(null); }} />
                <span><strong>{f.label}</strong> <span className="small muted">— {f.hint}</span></span>
              </label>
            ))}
          </fieldset>
          <div className="row" style={{ marginTop: 8 }}>
            <label className="field">Max events
              <input aria-label="Max events" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} style={{ width: 100 }} />
            </label>
            <label className="row"><input type="checkbox" checked={includeRaw} onChange={(e) => setIncludeRaw(e.target.checked)} /> Include raw payloads</label>
          </div>
          <Load state={schema}>
            {(s) => <p className="small muted">Contract <code>{s.schema_version}</code>. {s.semantics.completeness}</p>}
          </Load>
        </Card>
        <Card title="Export">
          <Load state={count}>
            {(d) => <Stat label="Matching events" value={d.total === null ? "—" : num(d.total)} hint="counted by views API" />}
          </Load>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" disabled={busy} onClick={() => run(null)}>{busy ? "Exporting…" : "Download"}</button>
            {cursor && <button disabled={busy} onClick={() => run(cursor)}>Download next page</button>}
          </div>
          {msg && <div className={`notice ${msg.kind}`} role="status" style={{ marginTop: 10 }}>{msg.text}</div>}
        </Card>
      </div>
      <Card title="Recent exports">
        <Load state={logs} isEmpty={(d) => d.items.length === 0} empty="No exports yet.">
          {(d) => (
            <table>
              <thead><tr><th>Started</th><th>By</th><th>Format</th><th>Rows</th><th>Status</th><th>Filters</th></tr></thead>
              <tbody>{d.items.map((l) => (
                <tr key={l.id}>
                  <td className="small">{fmtTime(l.started_at)}</td><td>{l.actor}</td><td>{l.format}</td>
                  <td>{num(l.rows)}{l.has_more ? " (more)" : ""}</td><td><Badge value={l.status} /></td>
                  <td className="small"><KV items={Object.entries(l.filters).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])} /></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Load>
      </Card>
    </>
  );
}
