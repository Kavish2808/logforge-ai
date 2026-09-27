// Event-level Phase 7 evidence: where the extensions live, the cold raw copy,
// Merkle sealing, and on-demand integrity verification.
import { useState } from "react";
import { getExtensions, verifyEvent } from "../api/endpoints";
import type { EventVerify, Lineage } from "../api/types";
import { shortHash } from "../lib/format";
import { errorMessage, useApi } from "../lib/useApi";
import { ExtensionStorage } from "./trust";
import { Badge, Card, Json, KV } from "./ui";

function OverflowFields({ id }: { id: string }) {
  const view = useApi((s) => getExtensions(id, s), [id]);
  if (view.error) return <div className="notice fail" role="alert">{view.error}</div>;
  if (!view.data) return <div className="small faint">Loading full extension set…</div>;
  return (
    <details>
      <summary className="small">All {view.data.total_field_count} preserved fields (inline + overflow;
        overflow integrity {view.data.overflow_integrity_verified ? "verified" : "NOT verified"})</summary>
      <Json value={view.data.extensions} />
    </details>
  );
}

export function EvidenceCard({ lineage }: { lineage: Lineage }) {
  const ev = lineage.evidence;
  const [result, setResult] = useState<EventVerify | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!ev) return null;
  const es = ev.extension_storage;
  return (
    <Card title="Evidence & integrity" actions={
      <button onClick={async () => { setError(null); try { setResult(await verifyEvent(lineage.event_id)); } catch (err) { setError(errorMessage(err)); } }}>
        Verify integrity
      </button>}>
      <KV items={[
        ["Extensions", <span key="e"><ExtensionStorage mode={es.mode} overflow={es.overflow_field_count} />{" "}
          <span className="small muted">{es.inline_field_count} inline{es.mode === "SPILLED" ? ` · ${es.overflow_field_count} in overflow (${es.overflow_bytes} bytes)` : ""}</span></span>],
        ["Raw storage", ev.raw_storage
          ? <span key="r"><Badge value={ev.raw_storage.tier} /> <span className="small muted">{ev.raw_storage.status}{ev.raw_storage.error ? ` — ${ev.raw_storage.error}` : ""}</span></span>
          : "not archived yet"],
        ["Cold object", ev.raw_storage?.object_key ? <code key="o" style={{ fontSize: 11 }}>{ev.raw_storage.object_key}</code> : null],
        ["Merkle", ev.merkle ? `batch #${ev.merkle.batch_seq} · leaf ${ev.merkle.leaf_index} · root ${shortHash(ev.merkle.root_hash)}` : "not sealed yet"],
      ]} />
      {es.mode === "SPILLED" && <OverflowFields id={lineage.event_id} />}
      {error && <div className="notice fail" role="alert">{error}</div>}
      {result && (
        <div className={`notice ${result.valid ? "ok" : "fail"}`} role="status" style={{ marginTop: 8 }}>
          <strong>{result.status.replace(/_/g, " ")}</strong> — SHA-256 {result.hash?.valid ? "valid" : "INVALID"}
          {result.cold_copy?.valid !== null && result.cold_copy?.valid !== undefined && <>; cold copy {result.cold_copy.valid ? "valid" : "INVALID"}</>}
          {result.merkle.sealed ? <>; Merkle inclusion {result.merkle.inclusion_valid ? "proven" : "FAILED"}; anchor {result.merkle.anchor_valid ? "matches" : "MISMATCH"}</> : "; not sealed yet"}.
        </div>
      )}
    </Card>
  );
}
