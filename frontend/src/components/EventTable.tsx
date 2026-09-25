import type { EventRow } from "../api/types";
import { fmtAgo, fmtClock, fmtDate, fmtTime, shortHash } from "../lib/format";
import { navigate } from "../lib/router";
import { Badge, Id } from "./ui";

/** Keyboard-accessible event table; a row opens Event Forensics. */
export function EventTable({ rows, compact }: { rows: EventRow[]; compact?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Status</th><th>Source</th>
            {!compact && <><th>Vendor / Product</th><th>Format / Category</th><th>Adapter</th></>}
            <th>Drift</th><th>Warnings</th>{!compact && <th>Raw SHA-256</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.event_id} className="clickable" tabIndex={0} aria-label={`Open event ${r.event_id}`}
              onClick={() => navigate("events", r.event_id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate("events", r.event_id); } }}>
              <td className="small nowrap" title={fmtTime(r.received_at)}>
                {compact ? fmtAgo(r.received_at) : <>{fmtDate(r.received_at)}<div className="muted">{fmtClock(r.received_at)}</div></>}
              </td>
              <td><Badge value={r.status} /></td>
              <td className="mono wrap-id">{r.source_key ? <Id value={r.source_key} /> : "—"}</td>
              {!compact && (
                <>
                  <td>{r.vendor ?? "—"}{r.product ? <div className="small muted">{r.product}</div> : null}</td>
                  <td><span className="chip">{r.format_detected}</span>{r.ocsf_category_name && <div className="small muted">{r.ocsf_category_name}</div>}</td>
                  <td className="mono small wrap-id">{r.adapter_id ? <><Id value={r.adapter_id} /><div className="muted">v{r.adapter_version}</div></> : "—"}</td>
                </>
              )}
              <td><Badge value={r.drift_status} />{r.drift_severity && <> <Badge value={r.drift_severity} /></>}</td>
              <td>{r.warning_count ? <span className="badge b-warn">{r.warning_count}</span> : <span className="faint">0</span>}</td>
              {!compact && <td className="mono small nowrap" title={r.raw_hash}>{shortHash(r.raw_hash)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
