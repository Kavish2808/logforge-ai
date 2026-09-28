// Drift Queue — Phase 5 human review. Actions call the existing
// POST /events/{id}/drift/accept; nothing changes without an explicit decision.
import { useState } from "react";
import { acceptDrift, getBaseline, getEvent, getEvents, proposeLearning } from "../api/endpoints";
import type { DriftRecord, EventRow } from "../api/types";
import { Phase8ReviewQueue } from "../components/reviewQueue";
import { ActingAs, SlaPanel } from "../components/trust";
import { Badge, Card, KV, Load, Pipeline } from "../components/ui";
import { fmtTime } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

function SimilarityBar({ similarity, threshold }: { similarity: number; threshold: number }) {
  return (
    <div>
      <div className="spread small"><span>Similarity {(similarity * 100).toFixed(1)}%</span><span className="muted">threshold {(threshold * 100).toFixed(0)}%</span></div>
      <div className="bar-track" style={{ height: 10, position: "relative" }} role="img"
        aria-label={`Similarity ${(similarity * 100).toFixed(1)} percent, threshold ${(threshold * 100).toFixed(0)} percent`}>
        <div className="bar-fill" style={{ width: `${similarity * 100}%`, background: similarity >= threshold ? "var(--ok)" : "var(--warn)" }} />
        <div style={{ position: "absolute", left: `${threshold * 100}%`, top: -3, bottom: -3, width: 2, background: "var(--text)" }} />
      </div>
    </div>
  );
}

function DriftDetail({ row, onDecided }: { row: EventRow; onDecided: () => void }) {
  const event = useApi((s) => getEvent(row.event_id, s), [row.event_id]);
  const baseline = useApi((s) => (row.source_key ? getBaseline(row.source_key, s) : Promise.resolve(null)), [row.source_key]);
  const [pending, setPending] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);

  const decide = async (mode: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await acceptDrift(row.event_id, mode, note || null);
      setMessage({ kind: "ok", text: `Recorded human decision: ${mode.replace("_", " ")}.` });
      setPending(null);
      onDecided();
      event.reload();
    } catch (err) {
      setMessage({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };
  const propose = async () => {
    setBusy(true);
    try {
      const s = await proposeLearning(row.event_id);
      navigate("learning", s.id);
    } catch (err) {
      setMessage({ kind: "fail", text: errorMessage(err) });
      setBusy(false);
    }
  };

  return (
    <Load state={event}>
      {(ev) => {
        const d = ev.processing_metadata.drift as DriftRecord | undefined;
        if (!d) return <div className="faint">This event has no drift record.</div>;
        const diff = d.differences ?? {};
        const underReview = ev.status === "UNDER_REVIEW";
        const formatDrift = d.status === "POSSIBLE_FORMAT_DRIFT";
        const learnable = d.status === "DRIFT" && ["accepted_variant", "replaced_baseline"].includes(d.review?.resolution ?? "");
        const eventFields = ev.structural_fingerprint?.field_order ?? [];
        const refFields = baseline.data?.fingerprint.field_order ?? [];
        return (
          <div className="grid" style={{ gap: 14 }}>
            <div className="spread">
              <div>
                <div className="row"><strong className="mono">{d.source_key}</strong><Badge value={d.status} />{d.severity && <Badge value={d.severity} title={`severity score ${d.severity_score}`} />}</div>
                <div className="small muted">{row.vendor ?? "—"} · received {fmtTime(row.received_at)} · baseline v{d.baseline_version ?? "—"} ({d.baseline_origin ?? "—"})</div>
              </div>
              <Link to="events" param={row.event_id}>Open forensics →</Link>
            </div>

            {typeof d.similarity === "number" && typeof d.threshold === "number" && <SimilarityBar similarity={d.similarity} threshold={d.threshold} />}

            <div>
              <h3>What changed</h3>
              <div className="row" style={{ marginBottom: 6 }}>{(d.change_types ?? []).map((t) => <span key={t} className="badge b-review">{t.replace(/_/g, " ")}</span>)}</div>
              <div className="row">
                {(diff.added_fields ?? []).map((f) => <span key={`a${f}`} className="chip add">+ {f}</span>)}
                {(diff.removed_fields ?? []).map((f) => <span key={`r${f}`} className="chip rem">− {f}</span>)}
                {Object.entries(diff.type_changes ?? {}).map(([f, c]) => <span key={`t${f}`} className="chip chg">~ {f}: {c.baseline} → {c.current}</span>)}
                {diff.order_changed && <span className="chip chg">↕ field order changed</span>}
                {diff.format_changed && <span className="chip rem">⇄ format {diff.format_changed.baseline} → {diff.format_changed.current}</span>}
              </div>
            </div>

            {(d.critical_field_changes ?? []).length > 0 && (
              <div className="notice fail">
                <strong>Critical fields affected:</strong>{" "}
                {d.critical_field_changes!.map((c) => `${c.field}${c.target ? ` (${c.target})` : ""} ${c.change.replace("_", " ")}`).join("; ")}
              </div>
            )}
            {formatDrift && (d.evidence ?? []).length > 0 && (
              <div className="notice warn">
                <strong>Possible format drift</strong> — routed to <span className="mono">{d.current_adapter}</span>. Evidence:
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{d.evidence!.map((e, i) => <li key={i}>{e.reason}: {e.detail}</li>)}</ul>
              </div>
            )}

            <div>
              <h3>Recommendation</h3>
              <div className="row">{(d.recommended_actions ?? []).map((a) => <span key={a} className="badge b-info">{a}</span>)}</div>
              {d.recommended_action && <p className="small muted" style={{ margin: "6px 0 0" }}>{d.recommended_action}</p>}
            </div>

            <details>
              <summary className="small">Compare against baseline reference ({refFields.length} vs {eventFields.length} fields)</summary>
              <div className="grid g2 small" style={{ marginTop: 8 }}>
                <div><h3>Baseline reference</h3>{refFields.map((f) => <div key={f} className={`mono ${eventFields.includes(f) ? "" : "chip rem"}`}>{f}</div>)}</div>
                <div><h3>This event</h3>{eventFields.map((f) => <div key={f} className={`mono ${refFields.includes(f) ? "" : "chip add"}`}>{f}</div>)}</div>
              </div>
            </details>
            {d.explanation && <details><summary className="small">Deterministic explanation</summary><pre className="code">{d.explanation}</pre></details>}

            {message && <div className={`notice ${message.kind}`} role="status">{message.text}</div>}

            {underReview && <SlaPanel itemType="DRIFT_EVENT" itemId={row.event_id} />}
            {underReview ? (
              <div className="boundary">
                <div className="spread"><strong>Human decision required</strong><span className="small muted">Nothing changes until you confirm.</span></div>
                <ActingAs />
                <p className="small muted" style={{ margin: "6px 0 10px" }}>
                  Accepting only updates the Phase 5 baseline. Changing how logs are parsed requires a separate, explicitly approved learning proposal.
                </p>
                <div className="row">
                  {!formatDrift && <button onClick={() => setPending("add_variant")} disabled={busy}>Accept as variant</button>}
                  {!formatDrift && <button onClick={() => setPending("replace_baseline")} disabled={busy}>Replace baseline</button>}
                  <button onClick={() => setPending("acknowledge")} disabled={busy}>Acknowledge</button>
                </div>
                {pending && (
                  <div className="row" style={{ marginTop: 10 }}>
                    <input aria-label="Decision note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
                    <button className="primary" onClick={() => decide(pending)} disabled={busy}>Confirm: {pending.replace("_", " ")}</button>
                    <button className="ghost" onClick={() => setPending(null)}>Cancel</button>
                  </div>
                )}
              </div>
            ) : (
              <div className="card" style={{ background: "var(--neutral-bg)" }}>
                <KV items={[["Human decision", d.review?.resolution ?? "—"], ["Reviewed", fmtTime(d.review?.reviewed_at)], ["Note", d.review?.note ?? null]]} />
                {learnable && (
                  <div className="row" style={{ marginTop: 10 }}>
                    <button className="primary" onClick={propose} disabled={busy}>Propose learning (Phase 6)</button>
                    <span className="small muted">Creates a proposal only — validation and a separate approval are required before any adapter change.</span>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      }}
    </Load>
  );
}

export function DriftQueue() {
  const [tab, setTab] = useState<"pending" | "reviewed">("pending");
  const [selected, setSelected] = useState<EventRow | null>(null);
  const pending = useApi((s) => getEvents({ status: "UNDER_REVIEW", limit: 100 }, s), []);
  const reviewed = useApi((s) => getEvents({ drift_status: "DRIFT", limit: 100 }, s), []);
  const list = tab === "pending" ? pending : reviewed;
  const refresh = () => { pending.reload(); reviewed.reload(); };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Drift Queue</h1>
          <p>Structural changes of known sources, detected deterministically by Phase 5 and resolved only by a human.</p>
        </div>
      </div>
      <Pipeline steps={["Detected", "Review", "Human decision", "Accepted / acknowledged", "Optional: learning proposal"]} current={2} />
      <div className="grid g2" style={{ marginTop: 16, gridTemplateColumns: "minmax(0,1fr) minmax(0,1.3fr)" }}>
        <Card title={
          <div className="tabs" role="tablist" style={{ margin: 0, border: "none" }}>
            <button role="tab" aria-selected={tab === "pending"} onClick={() => { setTab("pending"); setSelected(null); }}>
              Awaiting review ({pending.data?.items.length ?? "…"})
            </button>
            <button role="tab" aria-selected={tab === "reviewed"} onClick={() => { setTab("reviewed"); setSelected(null); }}>
              All drift ({reviewed.data?.items.length ?? "…"})
            </button>
          </div>}>
          <Load state={list} isEmpty={(d) => d.items.length === 0}
            empty={tab === "pending" ? "No events are awaiting review." : "No drift has been detected."}>
            {(d) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Source / received</th><th>Drift</th><th>Status</th></tr></thead>
                  <tbody>
                    {d.items.map((r) => (
                      <tr key={r.event_id} className={`clickable${selected?.event_id === r.event_id ? " selected" : ""}`} tabIndex={0}
                        onClick={() => setSelected(r)} aria-label={`Review drift ${r.event_id}`}
                        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(r); } }}>
                        <td><div className="mono">{r.source_key}</div><div className="small muted nowrap">{fmtTime(r.received_at)}</div></td>
                        <td><Badge value={r.drift_status} />{r.drift_severity && <> <Badge value={r.drift_severity} /></>}</td>
                        <td><Badge value={r.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Load>
        </Card>
        <Card title="Review">
          {selected ? <DriftDetail key={selected.event_id} row={selected} onDecided={refresh} /> : <div className="state">Select a drift event to review it.</div>}
        </Card>
      </div>
      <Phase8ReviewQueue />
    </>
  );
}
