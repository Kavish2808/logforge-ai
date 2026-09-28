// Phase 8 forensics: append-only revision lineage and compact lineage.
// Integrity verdicts (snapshot / raw SHA-256 / parent chain) come from the API.
import { getCompactLineage, getCompactLineageStats, getRevisions } from "../api/phase8";
import { fmtTime, num, shortHash } from "../lib/format";
import { Link } from "../lib/router";
import { useApi } from "../lib/useApi";
import { Check, StageStrip } from "./phase8";
import { Badge, Card, Json, KV, Load } from "./ui";

export function RevisionsCard({ eventId }: { eventId: string }) {
  const h = useApi((s) => getRevisions(eventId, s), [eventId]);
  return (
    <Card title="Revision history">
      <Load state={h}>
        {(d) => (
          <div className="grid" style={{ gap: 12 }}>
            <div className="row">
              <Check ok={d.integrity.all_snapshots_verified} yes="snapshots verified" no="snapshot mismatch" />
              <Check ok={d.integrity.raw_hash_unchanged} yes="raw SHA-256 unchanged" no="raw SHA-256 changed" />
              <Check ok={d.integrity.parent_chain_intact} yes="parent chain intact" no="parent chain broken" />
              <Check ok={d.integrity.single_current} yes="one current revision" no="multiple current" />
            </div>
            {d.count === 0 ? <div className="state">{d.note ?? "No revisions."}</div> : (
              <ol className="lineage-chain" aria-label="Revision lineage">
                {d.revisions.map((r) => (
                  <li key={r.id} className={r.is_current ? "current" : ""}>
                    <div className="spread">
                      <div className="row"><strong>Revision {r.revision_no}</strong><Badge value={r.trigger} /><Badge value={r.resulting_status} />
                        {r.is_current && <span className="badge b-ok">current</span>}</div>
                      <span className="small muted nowrap">{fmtTime(r.created_at)}</span>
                    </div>
                    <KV items={[
                      ["Adapter", r.adapter_id ? <span key="a" className="mono">{r.adapter_id} v{r.adapter_version ?? "—"}</span> : null],
                      ["Actor", r.actor], ["Reason", r.reason],
                      ["Replay job", r.replay_job_id ? <Link key="j" to="replay" param={r.replay_job_id}>{r.replay_job_id}</Link> : null],
                      ["Parent", r.parent_revision_id ? `revision ${d.revisions.find((p) => p.id === r.parent_revision_id)?.revision_no ?? "?"}` : "none (first)"],
                      ["Raw SHA-256", <span key="h" className="row"><code className="mono-break">{shortHash(r.raw_hash, 16)}</code>
                        <Check ok={r.raw_hash_matches_event} yes="matches event" no="differs" /></span>],
                      ["Snapshot SHA-256", <span key="s" className="row"><code className="mono-break">{shortHash(r.snapshot_sha256, 16)}</code>
                        <Check ok={r.snapshot_verified} yes="verified" no="modified" /></span>],
                    ]} />
                    <details style={{ marginTop: 6 }}><summary className="small">Snapshot</summary><Json value={r.snapshot} /></details>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </Load>
    </Card>
  );
}

export function CompactLineageCard({ eventId }: { eventId: string }) {
  const c = useApi((s) => getCompactLineage(eventId, true, s), [eventId]);
  return (
    <Card title="Compact lineage">
      <Load state={c}>
        {(d) => !d.decodable ? (
          <div className="notice fail" role="alert">Stored compact lineage could not be decoded: {d.error}</div>
        ) : (
          <div className="grid" style={{ gap: 10 }}>
            <StageStrip stages={d.stages ?? []} />
            <div className="row">
              {(d.exceptions ?? []).length ? d.exceptions!.map((x) => <Badge key={x} value={x} />) : <span className="small faint">no exception flags</span>}
              {d.verification && <Check ok={d.verification.equivalent} yes="matches detailed lineage" no="differs from detailed lineage now" />}
              {!d.persisted && <span className="badge b-info" title={d.note}>computed on read</span>}
            </div>
            {d.verification && d.verification.stage_differences.length > 0 && (
              <div className="notice warn">
                Point-in-time row: {d.verification.stage_differences.map((s) => `${s.stage} ${s.compact} → ${s.detailed_now} now`).join("; ")}.
              </div>
            )}
            <KV items={[
              ["Template", `v${d.template_version}`], ["Packed (5 bytes)", <code key="p">{d.packed_hex}</code>],
              ["Computed", fmtTime(d.computed_at)], ["Not in compact form", d.not_in_compact_form.join("; ")],
            ]} />
          </div>
        )}
      </Load>
    </Card>
  );
}

export function CompactLineageStatsCard() {
  const s = useApi((sig) => getCompactLineageStats(sig), []);
  return (
    <Card title="Compact lineage">
      <Load state={s}>
        {(d) => (
          <div className="grid" style={{ gap: 10 }}>
            <KV items={[
              ["Rows / events", `${num(d.rows)} / ${num(d.events_total)}${d.missing_rows ? ` (${num(d.missing_rows)} pending backfill)` : ""}`],
              ["Exception rows", `${num(d.exception_rows)} (normal ${num(d.normal_rows)})`],
              ["Table size", `${num(d.table_total_bytes)} bytes`],
              ["Template versions", Object.entries(d.by_template_version).map(([k, v]) => `v${k}: ${v}`).join(", ") || null],
            ]} />
            <div className="row">{Object.entries(d.by_exception).map(([k, v]) => (
              <span key={k} className={`badge ${v ? "b-warn" : ""}`}>{k.replace(/_/g, " ")} {v}</span>
            ))}</div>
          </div>
        )}
      </Load>
    </Card>
  );
}
