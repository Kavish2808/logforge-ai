// Export — UI foundation only. The export backend does not exist yet, so the
// download is disabled; the matching count is real (from /views/events).
import { useState } from "react";
import { getEvents } from "../api/endpoints";
import { EMPTY_FILTERS, EventFilterBar, EventFilterState, toQuery } from "../components/EventFilters";
import { Card, Load, Stat } from "../components/ui";
import { num } from "../lib/format";
import { useApi } from "../lib/useApi";

const FORMATS = [
  { id: "ndjson", label: "NDJSON", hint: "One stored UniversalEvent per line." },
  { id: "ocsf-ndjson", label: "OCSF-aligned NDJSON", hint: "The normalized OCSF-aligned event per line." },
];

export function ExportPage() {
  const [filters, setFilters] = useState<EventFilterState>(EMPTY_FILTERS);
  const [format, setFormat] = useState("ndjson");
  const count = useApi((s) => getEvents({ ...toQuery(filters), limit: 1, include_total: true }, s), [JSON.stringify(filters)]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Export</h1>
          <p>Select events and a format for export to downstream tools.</p>
        </div>
      </div>
      <div className="notice warn" role="note">
        <strong>Export is not available yet.</strong> The export backend has not been implemented — it is the next backend step.
        This page shows the selection only; no file is generated or downloaded.
      </div>
      <Card title="Selection">
        <EventFilterBar value={filters} onChange={setFilters} />
      </Card>
      <div className="grid g2">
        <Card title="Format">
          <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="small muted">Output format</legend>
            {FORMATS.map((f) => (
              <label key={f.id} className="row" style={{ padding: "4px 0" }}>
                <input type="radio" name="export-format" value={f.id} checked={format === f.id} onChange={() => setFormat(f.id)} />
                <span><strong>{f.label}</strong> <span className="small muted">— {f.hint}</span></span>
              </label>
            ))}
          </fieldset>
        </Card>
        <Card title="Export">
          <Load state={count}>
            {(d) => <Stat label="Matching events" value={d.total === null ? "—" : num(d.total)} hint="counted by the read-only views API" />}
          </Load>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" disabled aria-disabled="true" title="Export backend not implemented yet">Download (unavailable)</button>
            <span className="small muted">Available once the export backend ships.</span>
          </div>
        </Card>
      </div>
    </>
  );
}
