// Filter bar shared by the Event Explorer and Export pages. Options come from /views/filters.
import { useEffect, useState } from "react";
import { getFilters } from "../api/endpoints";
import type { FilterValues } from "../api/types";
import type { Query } from "../api/client";
import { toISO } from "../lib/format";
import { useApi } from "../lib/useApi";

export interface EventFilterState {
  start: string;
  end: string;
  status: string;
  source: string;
  vendor: string;
  product: string;
  format: string;
  adapter_id: string;
  adapter_version: string;
  drift_status: string;
  severity: string;
  category: string;
  search: string;
}

export const EMPTY_FILTERS: EventFilterState = {
  start: "", end: "", status: "", source: "", vendor: "", product: "", format: "", adapter_id: "",
  adapter_version: "", drift_status: "", severity: "", category: "", search: "",
};

export function toQuery(f: EventFilterState): Query {
  return {
    start: toISO(f.start), end: toISO(f.end), status: f.status, source: f.source, vendor: f.vendor,
    product: f.product, format: f.format, adapter_id: f.adapter_id, adapter_version: f.adapter_version,
    drift_status: f.drift_status, severity: f.severity, category: f.category,
    search: f.search.trim().length >= 3 ? f.search.trim() : undefined,
  };
}

export function EventFilterBar({ value, onChange }: { value: EventFilterState; onChange: (f: EventFilterState) => void }) {
  const options = useApi<FilterValues>((s) => getFilters(s), []);
  const [search, setSearch] = useState(value.search);

  // Debounce free-text search; everything else applies immediately.
  useEffect(() => {
    const t = setTimeout(() => { if (search !== value.search) onChange({ ...value, search }); }, 400);
    return () => clearTimeout(t);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setSearch(value.search), [value.search]);

  const o = options.data;
  const set = (k: keyof EventFilterState) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value });
  const select = (k: keyof EventFilterState, label: string, values: string[]) => (
    <label className="field">{label}
      <select value={value[k]} onChange={set(k)} aria-label={label}>
        <option value="">All</option>
        {values.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    </label>
  );
  const versions = Array.from(new Set((o?.adapters ?? [])
    .filter((a) => !value.adapter_id || a.adapter_id === value.adapter_id)
    .map((a) => a.adapter_version).filter((v): v is string => !!v))).sort();

  return (
    <div className="filters">
      <label className="field">Search
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="event id, SHA-256 or raw text (≥3)" aria-label="Search" />
      </label>
      <label className="field">From
        <input type="datetime-local" value={value.start} onChange={set("start")} aria-label="From" />
      </label>
      <label className="field">To
        <input type="datetime-local" value={value.end} onChange={set("end")} aria-label="To" />
      </label>
      {select("status", "Status", o?.statuses ?? [])}
      {select("source", "Source", o?.sources ?? [])}
      {select("vendor", "Vendor", o?.vendors ?? [])}
      {select("product", "Product", o?.products ?? [])}
      {select("format", "Format", o?.formats ?? [])}
      {select("adapter_id", "Adapter", Array.from(new Set((o?.adapters ?? []).map((a) => a.adapter_id))))}
      {select("adapter_version", "Adapter version", versions)}
      {select("drift_status", "Drift", [...(o?.drift_statuses ?? []), "NONE"])}
      {select("severity", "Severity", o?.severities ?? [])}
      {select("category", "Category", o?.categories ?? [])}
      <button onClick={() => { setSearch(""); onChange(EMPTY_FILTERS); }}>Reset filters</button>
    </div>
  );
}
