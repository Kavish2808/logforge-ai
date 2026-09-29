// Audit — the hash-chained governance audit log, with chain verification.
import { useState } from "react";
import { getAudit, verifyAudit } from "../api/endpoints";
import type { AuditRecord, AuditVerify } from "../api/types";
import { Badge, Card, Json, Load, Stat } from "../components/ui";
import { fmtTime, num, shortHash } from "../lib/format";
import { errorMessage, useApi } from "../lib/useApi";

function Row({ r, broken }: { r: AuditRecord; broken: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className={`clickable${broken ? " selected" : ""}`} tabIndex={0} onClick={() => setOpen(!open)} aria-expanded={open}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}>
        <td className="mono">{r.seq}</td>
        <td className="small nowrap">{fmtTime(r.timestamp)}</td>
        <td>{r.actor}{r.role && <div><Badge value={r.role} /></div>}{!r.authenticated && <div className="small faint">unauthenticated</div>}</td>
        <td className="mono small">{r.action}</td>
        <td className="small">{r.object_type}{r.object_id && <div className="mono muted">{r.object_id}</div>}</td>
        <td><Badge value={r.decision} /></td>
        <td className="mono small" title={r.current_hash}>{shortHash(r.current_hash, 10)}{broken && <div className="badge b-fail">chain break</div>}</td>
      </tr>
      {open && (
        <tr><td colSpan={7}>
          <div className="small" style={{ marginBottom: 6 }}>
            previous <code>{r.previous_hash}</code><br />current <code>{r.current_hash}</code>
            {r.evidence_ref && <><br />evidence <code>{r.evidence_ref}</code></>}
          </div>
          <Json value={r.details} />
        </td></tr>
      )}
    </>
  );
}

export function AuditPage() {
  const [action, setAction] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const page = useApi((s) => getAudit({ limit: 50, action: action || undefined, before_seq: before ?? undefined }, s), [action, before]);
  const [verify, setVerify] = useState<AuditVerify | null>(null);
  const [error, setError] = useState<string | null>(null);
  const broken = new Set((verify?.problems ?? []).map((p) => Number(p.seq)));
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Trust · tamper-evident</div>
          <h1>Audit log</h1>
          <p>Every governance action — approvals, rejections, drift decisions, rollbacks, role and configuration changes, exports — hash-chained and verifiable.</p>
        </div>
        <button className="primary" onClick={async () => {
          setError(null);
          try { setVerify(await verifyAudit()); } catch (err) { setError(errorMessage(err)); }
        }}>Verify chain</button>
      </div>
      {error && <div className="notice fail" role="alert">{error}</div>}
      {verify && (
        <div className={`notice ${verify.valid ? "ok" : "fail"}`} role="status">
          <strong>{verify.valid ? "Audit chain intact" : `Audit chain BROKEN at record ${verify.first_break_seq}`}</strong>
          {" "}— {num(verify.records_checked)} record(s) re-hashed and linked{verify.head_hash ? `; head ${shortHash(verify.head_hash)}` : ""}.
          {verify.problems.length > 0 && <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {verify.problems.map((p, i) => <li key={i}>record {String(p.seq)}: {p.problem}{p.detail ? ` — ${p.detail}` : ""}</li>)}
          </ul>}
        </div>
      )}
      <Load state={page}>
        {(d) => (
          <>
            <div className="stats">
              <Stat accent label="Audit records" value={num(d.stats.total)} />
              {Object.entries(d.stats.by_decision).map(([k, v]) => <Stat key={k} label={k} value={num(v)} />)}
              <Stat label="Head" value={d.stats.head ? `#${d.stats.head.seq}` : "—"} hint={d.stats.head ? shortHash(d.stats.head.hash) : undefined} />
            </div>
            <Card title="Records" actions={
              <label className="field">Action
                <input value={action} onChange={(e) => { setAction(e.target.value.toUpperCase()); setBefore(null); }} placeholder="e.g. ONBOARDING_APPROVE" aria-label="Filter by action" />
              </label>}>
              {d.items.length === 0 ? <div className="state">No audit records.</div> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Seq</th><th>Time</th><th>Actor / role</th><th>Action</th><th>Object</th><th>Decision</th><th>Hash</th></tr></thead>
                    <tbody>{d.items.map((r) => <Row key={r.seq} r={r} broken={broken.has(r.seq)} />)}</tbody>
                  </table>
                </div>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                <button disabled={before === null} onClick={() => setBefore(null)}>← Newest</button>
                <button disabled={!d.next_before_seq} onClick={() => setBefore(d.next_before_seq)}>Older →</button>
              </div>
            </Card>
          </>
        )}
      </Load>
    </>
  );
}
