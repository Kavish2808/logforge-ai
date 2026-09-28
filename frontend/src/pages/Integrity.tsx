// Integrity — Phase 7 evidence layer: Merkle evidence chain + anchors, cold raw
// vault (hot/cold distribution), and extension overflow with onboarding evidence.
import { useState } from "react";
import {
  backfillVault, getBatches, getOverflowEvidence, getTrust, overflowToOnboarding, recoverRaw, sealNow, verifyChain, verifyEvent,
} from "../api/endpoints";
import type { ChainVerify, EventVerify, RawRecovery } from "../api/types";
import { CompactLineageStatsCard } from "../components/forensics";
import { Badge, Card, KV, Load, Stat } from "../components/ui";
import { fmtTime, num, pct, shortHash } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

function Verdict({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  if (ok === null || ok === undefined) return <span className="badge b-neutral">{label}: n/a</span>;
  return <span className={`badge ${ok ? "b-ok" : "b-fail"}`}>{label}: {ok ? "valid" : "FAILED"}</span>;
}

function ChainCard({ onChanged }: { onChanged: () => void }) {
  const batches = useApi((s) => getBatches(s), []);
  const [result, setResult] = useState<ChainVerify | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setMsg(null);
    try { await fn(); } catch (err) { setMsg({ kind: "fail", text: errorMessage(err) }); } finally { setBusy(false); }
  };
  return (
    <Card title="Merkle evidence chain" actions={
      <div className="row">
        <button disabled={busy} onClick={() => run(async () => setResult(await verifyChain()))}>Verify chain</button>
        <button disabled={busy} onClick={() => run(async () => {
          const r = await sealNow(true);
          setMsg({ kind: "ok", text: r.sealed_batches.length ? `Sealed ${r.sealed_batches.length} batch(es).` : "Nothing to seal." });
          batches.reload(); onChanged();
        })}>Seal now</button>
      </div>}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Event hashes are grouped into Merkle batches; each batch root is chained to the previous one and written once to an
        append-only anchor store (local WORM-style files — not a compliance-grade WORM device).
      </p>
      {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
      {result && (
        <div className={`notice ${result.valid ? "ok" : "fail"}`} role="status">
          <strong>{result.valid ? "Chain verified" : "Chain verification FAILED"}</strong> — {result.batches_checked} batch(es),
          {" "}{num(result.events_sealed)} sealed event(s){result.sealed_events_since_deleted ? `, ${result.sealed_events_since_deleted} since deleted` : ""}.
          {result.problems.length > 0 && <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {result.problems.map((p, i) => <li key={i}><span className="mono">batch {String(p.seq)}</span>: {p.problem}{p.detail ? ` — ${p.detail}` : ""}</li>)}
          </ul>}
        </div>
      )}
      <Load state={batches} isEmpty={(d) => d.items.length === 0} empty="No batches sealed yet.">
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Seq</th><th>Window</th><th>Events</th><th>Root</th><th>Chain hash</th><th>Anchored</th></tr></thead>
              <tbody>{d.items.map((b) => (
                <tr key={b.seq}>
                  <td className="mono">{b.seq}</td>
                  <td className="small">{fmtTime(b.start_time)}<div className="muted">→ {fmtTime(b.end_time)}</div></td>
                  <td>{num(b.event_count)}</td>
                  <td className="mono small" title={b.root_hash}>{shortHash(b.root_hash)}</td>
                  <td className="mono small" title={b.chain_hash}>{shortHash(b.chain_hash)}</td>
                  <td>{b.anchored_at ? <span className="badge b-ok">anchored</span> : <span className="badge b-fail">not anchored</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Load>
    </Card>
  );
}

function EventCheck() {
  const [id, setId] = useState("");
  const [result, setResult] = useState<EventVerify | null>(null);
  const [raw, setRaw] = useState<RawRecovery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setError(null);
    try { await fn(); } catch (err) { setError(errorMessage(err)); }
  };
  const valid = /^[0-9A-HJKMNP-TV-Z]{26}$/.test(id.trim());
  return (
    <Card title="Verify one event">
      <div className="row">
        <input aria-label="Event id" placeholder="Event id (ULID)" value={id} onChange={(e) => setId(e.target.value.toUpperCase())} style={{ flex: 1 }} />
        <button disabled={!valid} onClick={() => run(async () => { setRaw(null); setResult(await verifyEvent(id.trim())); })}>Verify integrity</button>
        <button disabled={!valid} onClick={() => run(async () => setRaw(await recoverRaw(id.trim())))}>Recover raw from vault</button>
      </div>
      {error && <div className="notice fail" role="alert" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <Badge value={result.status} />
            <Verdict ok={result.hash?.valid} label="SHA-256" />
            <Verdict ok={result.cold_copy?.valid ?? null} label="Cold copy" />
            <Verdict ok={result.merkle.sealed ? result.merkle.inclusion_valid : null} label="Merkle inclusion" />
            <Verdict ok={result.merkle.sealed ? result.merkle.anchor_valid : null} label="Anchor" />
          </div>
          {result.merkle.sealed && <KV items={[
            ["Batch", `#${result.merkle.batch_seq} · leaf ${result.merkle.leaf_index}`],
            ["Root", <code key="r" style={{ fontSize: 11 }}>{result.merkle.root_hash}</code>],
            ["Proof length", `${result.merkle.proof?.length ?? 0} sibling hash(es)`],
          ]} />}
          <Link to="events" param={result.event_id}>Open forensics →</Link>
        </div>
      )}
      {raw && (
        <div className={`notice ${raw.recovered && raw.matches_event_hash ? "ok" : "fail"}`} style={{ marginTop: 10 }} role="status">
          {raw.recovered
            ? <>Recovered {raw.byte_size} bytes from the cold vault — SHA-256 {raw.matches_event_hash ? "matches" : "DOES NOT match"} the event;
              {" "}byte-identical to the hot copy: {raw.matches_hot_copy ? "yes" : "NO"}.</>
            : <>Not recovered: {raw.reason}</>}
        </div>
      )}
    </Card>
  );
}

export function IntegrityPage() {
  const trust = useApi((s) => getTrust(s), []);
  const evidence = useApi((s) => getOverflowEvidence(s), []);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const toOnboarding = async (id: number) => {
    try {
      const r = await overflowToOnboarding(id);
      navigate("onboarding", r.onboarding_session_id);
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    }
  };
  const backfill = async () => {
    try {
      const r = await backfillVault();
      setMsg({ kind: r.failed ? "warn" : "ok", text: `Archived ${r.archived} event(s); ${r.failed} failed.` });
      trust.reload();
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    }
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Integrity</h1>
          <p>Evidence you can verify: SHA-256 per event, a chained Merkle evidence log with append-only anchors, a cold raw vault, and lossless extension overflow.</p>
        </div>
      </div>
      {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
      <Load state={trust}>
        {(t) => (
          <div className="stats">
            <Stat accent label="Sealed events" value={num(t.integrity.events_sealed)} hint={`${num(t.integrity.batches)} batch(es) · ${num(t.integrity.events_unsealed)} awaiting seal`} />
            <Stat label="Chain head" value={t.integrity.head ? `#${t.integrity.head.seq}` : "—"} hint={t.integrity.head ? shortHash(t.integrity.head.chain_hash) : "no batches yet"} />
            <Stat label="Raw: hot (PostgreSQL)" value={num(t.raw_vault.hot)} hint="every event keeps its raw payload" />
            <Stat label="Raw: cold (vault)" value={num(t.raw_vault.cold_stored)} hint={`${pct(t.raw_vault.cold_stored, t.raw_vault.events_total)} · ${num(t.raw_vault.cold_bytes)} bytes`} />
            <Stat label="Vault failures" value={num(t.raw_vault.cold_failed)} hint={`${num(t.raw_vault.not_yet_archived)} not yet archived`} />
            <Stat label="Spilled events" value={num(t.extension_overflow.events_spilled)}
              hint={`${num(t.extension_overflow.overflow_field_count)} fields · ${num(t.extension_overflow.overflow_bytes)} bytes in overflow`} />
          </div>
        )}
      </Load>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <ChainCard onChanged={trust.reload} />
        <div>
          <EventCheck />
          <Card title="Cold raw vault" actions={<button onClick={backfill}>Archive missing / retry</button>}>
            <Load state={trust}>
              {(t) => (
                <KV items={[
                  ["Enabled", t.raw_vault.enabled ? "yes" : "no (HOT_ONLY)"],
                  ["Backend", String(t.raw_vault.backend.backend ?? "—")],
                  ["Addressing", "content-addressed by SHA-256 (sha256/aa/bb/<digest>)"],
                  ["Distribution", Object.entries(t.raw_vault.by_tier_status).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"],
                ]} />
              )}
            </Load>
          </Card>
          <CompactLineageStatsCard />
        </div>
      </div>
      <Card title="Extension overflow → onboarding evidence">
        <Load state={trust}>
          {(t) => <p className="small muted" style={{ marginTop: 0 }}>
            Inline budget: {num(t.extension_overflow.budget.max_fields)} fields / {num(t.extension_overflow.budget.max_bytes)} bytes per event.
            Beyond it, fields move to overflow storage — never truncated. {t.extension_overflow.note}
          </p>}
        </Load>
        <Load state={evidence} isEmpty={(d) => d.items.length === 0} empty="No extension overflow recorded.">
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Adapter</th><th>Overflow keys</th><th>Occurrences</th><th>Last seen</th><th>Evidence</th><th /></tr></thead>
                <tbody>{d.items.map((s) => (
                  <tr key={s.id}>
                    <td className="mono">{s.adapter_id}</td>
                    <td className="small" title={s.keys.join(", ")}>{s.key_count} key(s): {s.keys.slice(0, 4).join(", ")}{s.key_count > 4 ? "…" : ""}</td>
                    <td>{num(s.occurrences)}</td>
                    <td className="small">{fmtTime(s.last_seen)}</td>
                    <td>{s.onboarding_evidence ? <span className="badge b-review">onboarding evidence</span> : <span className="small faint">below threshold</span>}</td>
                    <td>{s.onboarding_session_id
                      ? <Link to="onboarding" param={s.onboarding_session_id}>session →</Link>
                      : s.onboarding_evidence && <button onClick={() => toOnboarding(s.id)}>Start onboarding</button>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Load>
      </Card>
    </>
  );
}
