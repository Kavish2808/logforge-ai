// Baseline Integrity — CURRENT (Phase 5, evolves through review/learning) vs
// GOLDEN (explicitly pinned). Pin / re-pin / retire are sent to the backend,
// which alone enforces SOC_ADMIN + note, maker-checker and auditing.
import { useState } from "react";
import {
  compareWithGolden, getCurrentBaseline, getCurrentBaselines, getGoldenBaselines, getGoldenDetail, getGuardComparisons,
  pinGolden, repinGolden, retireGolden, type GoldenBaseline, type GoldenPolicy,
} from "../api/phase8";
import { ConfirmAction, fmtNum } from "../components/phase8";
import { ActingAs } from "../components/trust";
import { Badge, Card, KV, Load } from "../components/ui";
import { fmtTime } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { useApi } from "../lib/useApi";

function Similarity({ value, threshold }: { value: number | null | undefined; threshold: number }) {
  if (typeof value !== "number") return <span className="faint">—</span>;
  return <span className={`badge ${value >= threshold ? "b-ok" : "b-fail"}`}>{(value * 100).toFixed(1)}% (threshold {(threshold * 100).toFixed(0)}%)</span>;
}

function Compare({ source, policy }: { source: string; policy: GoldenPolicy | null }) {
  const cmp = useApi((s) => compareWithGolden(source, s), [source]);
  const threshold = policy?.golden_similarity_threshold ?? 0.7;
  return (
    <Card title="Current vs golden">
      <Load state={cmp}>
        {(c) => (
          <div className="grid" style={{ gap: 12 }}>
            {c.next_change_would_be_elevated
              ? <div className="notice warn" role="status">The next baseline change for this source will require <strong>elevated review</strong> ({c.reasons.join(", ")}).</div>
              : <div className="notice ok" role="status">Within golden limits: the next change follows the normal review path.</div>}
            <KV items={[
              ["Golden", `v${c.golden.version}`],
              ["Current baseline", c.current_baseline_version !== null ? `v${c.current_baseline_version}` : null],
              ["Structural similarity", <Similarity key="s" value={c.structural?.similarity} threshold={threshold} />],
              ["Accepted changes since golden", `${c.steps_since_golden} (elevated above ${policy?.max_changes_since_golden ?? 5})`],
            ]} />
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{c.explanation.map((e) => <li key={e}>{e}</li>)}</ul>
            {c.changes_since_golden.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Version</th><th>Change</th><th>Event</th><th>Note</th><th>When</th></tr></thead>
                  <tbody>{c.changes_since_golden.map((h) => (
                    <tr key={`${h.version}-${h.action}`}><td>v{h.version}</td><td><span className="chip">{h.action}</span></td>
                      <td className="small">{h.event_id ? <Link to="events" param={h.event_id}>{h.event_id}</Link> : "—"}</td>
                      <td className="small">{h.note ?? "—"}</td><td className="small nowrap">{fmtTime(h.created_at)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {c.statistical.compared && c.statistical.fields && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Field</th><th>PSI vs golden</th><th>Null rate golden → now</th></tr></thead>
                  <tbody>{Object.entries(c.statistical.fields).map(([f, v]) => (
                    <tr key={f}><td className="mono small">{f}</td>
                      <td>{v.psi === null ? <span className="faint">—</span> : <span className={`badge ${v.psi_exceeded ? "b-fail" : "b-ok"}`}>{fmtNum(v.psi)}</span>}</td>
                      <td className="small">{fmtNum(v.golden_null_rate)} → {fmtNum(v.current_null_rate)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Load>
    </Card>
  );
}

function GuardEvidence({ source }: { source: string }) {
  const rows = useApi((s) => getGuardComparisons({ source_key: source, limit: 50 }, s), [source]);
  return (
    <Card title="Poisoning-guard evidence">
      <p className="small muted" style={{ marginTop: 0 }}>Every drift / learning action evaluated against the golden baseline, with the guard decision the backend recorded.</p>
      <Load state={rows} isEmpty={(d) => d.items.length === 0} empty="No guarded action has been evaluated against a golden baseline for this source.">
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Action</th><th>Decision</th><th>New vs golden</th><th>Changes since golden</th><th>Reasons</th></tr></thead>
              <tbody>{d.items.map((c) => (
                <tr key={c.id}>
                  <td className="small nowrap">{fmtTime(c.created_at)}</td>
                  <td className="small"><span className="chip">{c.action}</span><div className="faint mono-break">{c.object_type}:{c.object_id}</div></td>
                  <td><Badge value={c.decision} /></td>
                  <td className="small">{fmtNum(c.new_vs_golden?.similarity ?? null)}</td>
                  <td className="small">{c.steps_since_golden ?? "—"}</td>
                  <td className="small">{c.risk_reasons.length ? c.risk_reasons.map((r) => <div key={r.code}>{r.detail}</div>) : <span className="faint">none</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Load>
    </Card>
  );
}

function GoldenActions({ source, active, hasCurrent, onChanged }: { source: string; active: GoldenBaseline | null; hasCurrent: boolean; onChanged: () => void }) {
  const warn = "Requires an authenticated SOC_ADMIN and a written note; the backend refuses anything else and audits every attempt.";
  return (
    <div className="boundary">
      <div className="spread"><strong>Golden baseline governance</strong><span className="small muted">Nothing changes without confirmation.</span></div>
      <ActingAs />
      <div className="row" style={{ alignItems: "flex-start" }}>
        {!active && (
          <ConfirmAction label="Pin golden baseline" noteLabel="Why is this the trusted reference?" noteRequired disabled={!hasCurrent}
            description={<>Snapshots the current Phase 5 baseline of <code>{source}</code> as golden v1. {warn}</>}
            onConfirm={async (note) => { const g = await pinGolden(source, note); onChanged(); return `Pinned golden v${g.version}.`; }} />
        )}
        {active && (
          <ConfirmAction label="Re-pin golden baseline" noteLabel="Reason for re-pinning" noteRequired danger
            description={<>Replaces golden v{active.version} with the current baseline (v{active.version} is kept as SUPERSEDED). Maker-checker: whoever approved changes since v{active.version} cannot re-pin. {warn}</>}
            onConfirm={async (note) => { const g = await repinGolden(source, note, active.version); onChanged(); return `Re-pinned as golden v${g.version}.`; }} />
        )}
        {active && (
          <ConfirmAction label="Retire golden baseline" noteLabel="Reason for retiring" noteRequired danger
            description={<>Withdraws golden v{active.version}. The poisoning guard stops protecting <code>{source}</code> until a new golden is pinned. {warn}</>}
            onConfirm={async (note) => { await retireGolden(source, note, active.version); onChanged(); return `Golden v${active.version} retired.`; }} />
        )}
      </div>
      {!hasCurrent && !active && <p className="small muted">This source has no Phase 5 baseline yet, so there is nothing to pin.</p>}
    </div>
  );
}

function Detail({ source, policy }: { source: string; policy: GoldenPolicy | null }) {
  const current = useApi((s) => getCurrentBaseline(source, s), [source]);
  const golden = useApi((s) => getGoldenDetail(source, s).catch((e) => { if (e?.status === 404) return null; throw e; }), [source]);
  const [nonce, setNonce] = useState(0);
  const refresh = () => { current.reload(); golden.reload(); setNonce((n) => n + 1); };
  const active = golden.data?.active ?? null;
  return (
    <>
      <div className="grid g2">
        <Card title={<span className="row">Current baseline <span className="small muted">(Phase 5)</span></span>}>
          <Load state={current}>
            {(b) => (
              <KV items={[
                ["Version", `v${b.version}`], ["Origin", b.origin.replace(/_/g, " ")],
                ["Adapter", <span key="a" className="mono">{b.adapter_id}{b.adapter_version ? ` v${b.adapter_version}` : ""}</span>],
                ["Reference fields", b.fingerprint.field_count ?? (b.fingerprint.field_order ?? []).length],
                ["Accepted variants", b.accepted_variants.length],
                ["Awaiting review", b.under_review_count],
                ["Updated", fmtTime(b.updated_at)],
              ]} />
            )}
          </Load>
        </Card>
        <Card title={<span className="row">Golden baseline {active ? <Badge value={active.status} /> : null}</span>}>
          {!golden.loading && !golden.error && golden.data === null && (
            <div className="faint">No golden baseline has ever been pinned for this source. Drift and learning actions follow the normal review path.</div>
          )}
          <Load state={golden}>
            {(g) => !g || !g.active ? (
              <div className="faint">No active golden baseline{g && g.versions.length ? ` (latest v${g.versions[0].version} is ${g.versions[0].status})` : ""}. Drift and learning actions follow the normal review path.</div>
            ) : (
              <KV items={[
                ["Version", `v${g.active.version}`],
                ["Pinned from", `Phase 5 baseline v${g.active.derived_from_baseline_version ?? "—"}`],
                ["Approved by", `${g.active.approved_by} (${g.active.approved_role ?? "—"})`],
                ["Note", g.active.note],
                ["Adapter context", <span key="c" className="mono">{String(g.active.evidence.adapter_id ?? "—")} v{String(g.active.evidence.adapter_version ?? "—")}</span>],
                ["Statistical profile", g.active.statistical_profile.sufficient ? `${g.active.statistical_profile.n} events` : `insufficient (${g.active.statistical_profile.n ?? 0} < ${g.active.statistical_profile.min_sample ?? 200} events)`],
                ["Pinned", fmtTime(g.active.created_at)],
              ]} />
            )}
          </Load>
          {golden.data && golden.data.versions.length > 1 && (
            <details style={{ marginTop: 8 }}>
              <summary className="small">All golden versions ({golden.data.versions.length})</summary>
              <table><tbody>{golden.data.versions.map((v) => (
                <tr key={v.id}><td>v{v.version}</td><td><Badge value={v.status} /></td><td className="small">{v.approved_by}</td><td className="small nowrap">{fmtTime(v.created_at)}</td></tr>
              ))}</tbody></table>
            </details>
          )}
        </Card>
      </div>
      {active && <Compare key={`c${nonce}`} source={source} policy={policy} />}
      {!golden.loading && !golden.error && (
        <GoldenActions source={source} active={active} hasCurrent={!!current.data} onChanged={refresh} />
      )}
      <GuardEvidence key={`g${nonce}`} source={source} />
    </>
  );
}

export function Baselines({ sourceKey }: { sourceKey: string | null }) {
  const current = useApi((s) => getCurrentBaselines(s), []);
  const golden = useApi((s) => getGoldenBaselines(s), []);
  const goldenBy = Object.fromEntries((golden.data?.items ?? []).map((g) => [g.source_key, g]));
  const policy = golden.data?.policy ?? null;
  return (
    <>
      <div className="page-head">
        <div>
<div className="eyebrow">Adapt · baseline integrity</div>
          <h1>Baseline Integrity</h1>
          <p>CURRENT baselines evolve through human drift review and learning; GOLDEN baselines are pinned references that guard against gradual poisoning.</p>
        </div>
      </div>
      {policy && (
        <div className="notice">
          Poisoning guard: an action on {policy.guarded_actions.join(", ")} becomes <strong>ELEVATED</strong> when the new structure is below
          {" "}{(policy.golden_similarity_threshold * 100).toFixed(0)}% similar to the golden baseline or more than {policy.max_changes_since_golden} changes
          were accepted since it. Elevated review requires {policy.elevated_requires}.
        </div>
      )}
      <Card title="Sources">
        <Load state={current} isEmpty={(d) => d.items.length === 0} empty="No Phase 5 baselines yet. A baseline is created by the first event of a known source.">
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Source</th><th>Current</th><th>Variants</th><th>Awaiting review</th><th>Golden</th><th>Updated</th></tr></thead>
                <tbody>{d.items.map((b) => {
                  const g = goldenBy[b.source_key];
                  return (
                    <tr key={b.source_key} className={`clickable${sourceKey === b.source_key ? " selected" : ""}`} tabIndex={0}
                      onClick={() => navigate("baselines", b.source_key)} aria-label={`Baselines of ${b.source_key}`}
                      onKeyDown={(e) => { if (e.key === "Enter") navigate("baselines", b.source_key); }}>
                      <td className="mono">{b.source_key}</td>
                      <td>v{b.version} <span className="small faint">{b.origin.replace(/_/g, " ")}</span></td>
                      <td>{b.accepted_variants.length}</td>
                      <td>{b.under_review_count ? <Badge value="UNDER_REVIEW" /> : 0}</td>
                      <td>{g ? <><Badge value="ACTIVE" /> v{g.version}</> : golden.error ? <span className="faint">unavailable</span> : <span className="faint">not pinned</span>}</td>
                      <td className="small nowrap">{fmtTime(b.updated_at)}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          )}
        </Load>
      </Card>
      {sourceKey && <Detail key={sourceKey} source={sourceKey} policy={policy} />}
    </>
  );
}
