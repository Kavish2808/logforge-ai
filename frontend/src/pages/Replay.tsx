// Replay & revisions — rate-limited replay jobs through the existing reprocess
// path, and revision-aware rollback. The scheduler drives RUNNING jobs; this
// page only creates / transitions them. Governance (roles, the >10,000-event
// maker-checker, reasons) is enforced and audited by the backend.
import { useState } from "react";
import { createReplayJob, getReplayJob, listReplayJobs, replayAction, revisionRollback, type ReplayJob } from "../api/phase8";
import { Check, ConfirmAction, Progress } from "../components/phase8";
import { ActingAs } from "../components/trust";
import { Badge, Card, KV, Load } from "../components/ui";
import { fmtTime, num } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { useApi } from "../lib/useApi";

function JobActions({ job, onChanged }: { job: ReplayJob; onChanged: () => void }) {
  const act = (action: "start" | "pause" | "resume" | "cancel") => async () => {
    const j = await replayAction(job.id, action);
    onChanged();
    return `Replay job is now ${j.status.replace(/_/g, " ")}.`;
  };
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      {(job.status === "PENDING" || job.status === "PENDING_APPROVAL") && (
        <ConfirmAction label="Start replay"
          description={job.status === "PENDING_APPROVAL"
            ? `This job re-processes ${num(job.total)} events (> 10,000): it must be started by an authenticated SECURITY_ENGINEER or SOC_ADMIN other than its creator (${job.created_by}).`
            : `Re-processes up to ${num(job.total)} stored events at ≤ ${job.rate_per_sec}/s (batches of ${job.batch_size}). Raw bytes never change; each event gets a new revision.`}
          onConfirm={act("start")} />
      )}
      {job.status === "RUNNING" && <ConfirmAction label="Pause" description="Stops after the current event; the checkpoint is kept." onConfirm={act("pause")} />}
      {job.status === "PAUSED" && <ConfirmAction label="Resume" description="Continues from the last durable checkpoint." onConfirm={act("resume")} />}
      {!["COMPLETED", "CANCELLED", "FAILED"].includes(job.status) && (
        <ConfirmAction label="Cancel" danger description="Stops the job permanently. Revisions already written are kept; unprocessed events are untouched." onConfirm={act("cancel")} />
      )}
    </div>
  );
}

function JobDetail({ id }: { id: string }) {
  const job = useApi((s) => getReplayJob(id, s), [id]);
  return (
    <Card title="Replay job" actions={<button onClick={job.reload}>Refresh</button>}>
      <Load state={job}>
        {(j) => (
          <div className="grid" style={{ gap: 12 }}>
            <div className="row"><Badge value={j.status} /><Badge value={j.trigger} />{j.large_replay && <span className="badge b-review">&gt; 10,000 events</span>}
              <span className="mono small">{j.adapter_id} → v{j.target_version ?? "—"}</span></div>
            <Progress done={j.processed} total={j.total} label="Processed" />
            <div className="row">
              <span className="badge b-ok">succeeded {j.succeeded}</span>
              <span className={`badge ${j.failed ? "b-fail" : ""}`}>failed {j.failed}</span>
              <span className="badge">skipped {j.skipped} (already replayed)</span>
              <Check ok={j.integrity.raw_hash_mismatches === 0} yes="raw SHA-256 preserved" no={`${j.integrity.raw_hash_mismatches} raw hash mismatch`} />
              {j.integrity.merkle && <Check ok={j.integrity.merkle.valid} yes={`Merkle valid (${j.integrity.merkle.scope})`} no="Merkle verification failed" />}
            </div>
            {j.error && <div className="notice fail" role="alert">{j.error}</div>}
            <KV items={[
              ["Reason", j.reason],
              ["Selection", `${j.selection.from_version === "*" ? "any version" : `processed by v${j.selection.from_version}`}${j.selection.window_start ? ` from ${fmtTime(j.selection.window_start)}` : ""}${j.selection.window_end ? ` until ${fmtTime(j.selection.window_end)}` : ""} · received before ${fmtTime(j.selection.selection_cutoff)}`],
              ["Rate limit", `≤ ${j.rate_per_sec} events/s · batch ${j.batch_size} · ${j.slices} slice(s)`],
              ["Checkpoint", j.checkpoint ? <span key="c" className="mono-break">{fmtTime(j.checkpoint.received_at)} · {j.checkpoint.event_id}</span> : "not started"],
              ["Revisions written", j.integrity.revisions_written],
              ["Created", `${fmtTime(j.created_at)} by ${j.created_by}`],
              ["Started", j.started_at ? `${fmtTime(j.started_at)}${j.approved_by ? ` (approved by ${j.approved_by})` : ""}` : null],
              ["Completed", fmtTime(j.completed_at)],
            ]} />
            {j.errors.length > 0 && (
              <details><summary className="small">Per-event errors ({j.errors.length})</summary>
                <ul className="small">{j.errors.map((e) => <li key={e.event_id}><Link to="events" param={e.event_id}>{e.event_id}</Link>: {e.error}</li>)}</ul>
              </details>
            )}
            <ActingAs />
            <JobActions job={j} onChanged={job.reload} />
          </div>
        )}
      </Load>
    </Card>
  );
}

function NewJob({ onCreated }: { onCreated: (j: ReplayJob) => void }) {
  const [adapter, setAdapter] = useState("");
  const [fromVersion, setFromVersion] = useState("");
  const [rate, setRate] = useState("50");
  const [batch, setBatch] = useState("100");
  return (
    <Card title="New replay job">
      <div className="filters">
        <label className="field">Adapter / source<input value={adapter} onChange={(e) => setAdapter(e.target.value)} aria-label="Adapter id" /></label>
        <label className="field">Only events of version<input value={fromVersion} onChange={(e) => setFromVersion(e.target.value)} placeholder="any" aria-label="From version" /></label>
        <label className="field">Max events / second<input type="number" min={1} max={1000} value={rate} onChange={(e) => setRate(e.target.value)} aria-label="Rate per second" /></label>
        <label className="field">Batch size<input type="number" min={1} max={1000} value={batch} onChange={(e) => setBatch(e.target.value)} aria-label="Batch size" /></label>
      </div>
      <div style={{ marginTop: 10 }}>
        <ConfirmAction label="Create replay job" noteLabel="Reason" noteRequired disabled={!adapter.trim()}
          description="Creates a PENDING job (nothing runs until it is started). Over 10,000 events it needs an authenticated SECURITY_ENGINEER / SOC_ADMIN and a second approver to start."
          onConfirm={async (reason) => {
            const j = await createReplayJob({ adapter_id: adapter.trim(), reason, from_version: fromVersion.trim() || null,
              rate_per_sec: Number(rate) || undefined, batch_size: Number(batch) || undefined });
            onCreated(j);
            return `Created ${j.status.replace(/_/g, " ")} job for ${num(j.total)} event(s).`;
          }} />
      </div>
    </Card>
  );
}

function Rollback({ onCreated }: { onCreated: (j: ReplayJob) => void }) {
  const [adapter, setAdapter] = useState("");
  const [replay, setReplay] = useState(true);
  return (
    <Card title="Revision-aware rollback">
      <p className="small muted" style={{ marginTop: 0 }}>
        Restores the previous approved version of an onboarded adapter through the existing Phase 3 / Phase 6 rollback, with before/after
        snapshots and Merkle checks. Events are not modified by the rollback; an optional ROLLBACK replay job (created PENDING) re-processes the
        events the withdrawn version produced, recording new revisions.
      </p>
      <div className="row">
        <label className="field">Onboarded adapter<input value={adapter} onChange={(e) => setAdapter(e.target.value)} aria-label="Rollback adapter id" /></label>
        <label className="row small"><input type="checkbox" checked={replay} onChange={(e) => setReplay(e.target.checked)} /> queue a replay job</label>
      </div>
      <div style={{ marginTop: 10 }}>
        <ConfirmAction label="Roll back adapter" danger noteLabel="Reason" noteRequired disabled={!adapter.trim()}
          description="Requires the rollback capability (SECURITY_ENGINEER or above); audited as REVISION_ROLLBACK."
          onConfirm={async (reason) => {
            const r = await revisionRollback(adapter.trim(), { reason, replay });
            if (r.replay_job) onCreated(r.replay_job);
            return `Rolled back v${r.rolled_back_version} → v${r.restored_version}.${r.replay_job ? ` Replay job ${r.replay_job.id} created (PENDING).` : ""}`;
          }} />
      </div>
    </Card>
  );
}

export function ReplayPage({ jobId }: { jobId: string | null }) {
  const jobs = useApi((s) => listReplayJobs(s), []);
  const created = (j: ReplayJob) => { jobs.reload(); navigate("replay", j.id); };
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Investigate · forensics</div>
          <h1>Replay &amp; Revisions</h1>
          <p>Rate-limited, resumable replay through the existing reprocess path. Every replayed event keeps its raw bytes and gains an append-only revision.</p>
        </div>
      </div>
      <div className="grid g-main">
        <Card title="Replay jobs" actions={<button onClick={jobs.reload}>Refresh</button>}>
          <Load state={jobs} isEmpty={(d) => d.items.length === 0} empty="No replay jobs yet.">
            {(d) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Job</th><th>Status</th><th>Progress</th><th>Trigger</th><th>Created</th></tr></thead>
                  <tbody>{d.items.map((j) => (
                    <tr key={j.id} className={`clickable${jobId === j.id ? " selected" : ""}`} tabIndex={0} onClick={() => navigate("replay", j.id)}
                      aria-label={`Replay job ${j.id}`} onKeyDown={(e) => { if (e.key === "Enter") navigate("replay", j.id); }}>
                      <td><div className="mono small">{j.adapter_id}</div><div className="faint small mono-break">{j.id}</div></td>
                      <td><Badge value={j.status} /></td>
                      <td className="small nowrap">{num(j.processed)} / {num(j.total)}</td>
                      <td><Badge value={j.trigger} /></td>
                      <td className="small nowrap">{fmtTime(j.created_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </Load>
        </Card>
        <div>{jobId ? <JobDetail key={jobId} id={jobId} /> : <Card><div className="state">Select a job to see its progress and checkpoint.</div></Card>}</div>
      </div>
      <div className="grid g2">
        <NewJob onCreated={created} />
        <Rollback onCreated={created} />
      </div>
      <p className="small faint">Per-event revision history is on each event's forensics page (<Link to="events">Event Explorer</Link>).</p>
    </>
  );
}
