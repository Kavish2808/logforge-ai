// Shared presentational components. They only render values they are given.
import { ReactNode, useId, useState } from "react";
import type { ApiState } from "../lib/useApi";
import { pct } from "../lib/format";

const STATUS_CLASS: Record<string, string> = {
  SUCCESS: "b-ok", PARTIAL: "b-warn", FAILED: "b-fail", UNDER_REVIEW: "b-review",
  NORMAL: "b-ok", BASELINE_CREATED: "b-info", DRIFT: "b-warn", POSSIBLE_FORMAT_DRIFT: "b-fail", ERROR: "b-fail",
  LOW: "b-info", MEDIUM: "b-warn", HIGH: "b-fail", CRITICAL: "b-fail",
  PASSED: "b-ok", NEEDS_REVIEW: "b-warn", REJECTED: "b-fail",
  ACTIVE: "b-ok", APPROVED: "b-info", VALIDATED: "b-info", PROPOSED: "b-review", ROLLED_BACK: "b-neutral",
  SUPERSEDED: "b-neutral", NO_CHANGE_REQUIRED: "b-neutral", COLLECTED: "b-neutral", SUGGESTION_FAILED: "b-fail",
  OK: "b-ok", WARN: "b-warn", FAIL: "b-fail", SKIPPED: "b-neutral",
  // Phase 7
  PENDING: "b-info", DUE_SOON: "b-warn", OVERDUE: "b-fail", ESCALATED: "b-fail", RESOLVED: "b-neutral",
  INLINE: "b-ok", SPILLED: "b-warn", STORED: "b-ok", HOT_AND_COLD: "b-ok", HOT_ONLY: "b-warn",
  VERIFIED: "b-ok", HASH_VALID_UNSEALED: "b-info", INTEGRITY_FAILURE: "b-fail", EVENT_DELETED: "b-neutral",
  OPEN: "b-fail", ACKNOWLEDGED: "b-neutral", DENIED: "b-fail", INFO: "b-info", COMPLETED: "b-ok", STARTED: "b-info",
  ANALYST: "b-info", SECURITY_ENGINEER: "b-review", SOC_ADMIN: "b-warn",
  // Phase 8
  REVIEW_REQUIRED: "b-warn", BLOCKED: "b-fail", RUNNING: "b-info", PAUSED: "b-warn", CANCELLED: "b-neutral",
  PENDING_APPROVAL: "b-review", RETIRED: "b-neutral", ELEVATED: "b-warn", ALLOWED: "b-ok", ADVISORY: "b-review",
  STATISTICAL: "b-info", SEMANTIC: "b-review", STRUCTURAL: "b-warn", ORIGINAL: "b-neutral", REPLAY: "b-info",
  REPROCESS: "b-info", ROLLBACK: "b-warn", MANUAL: "b-neutral", OVERFLOW: "b-warn", VAULT_FAILED: "b-fail",
  LEARNING: "b-review", REVIEWED: "b-neutral", DISMISSED: "b-neutral",
};

export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return <span className="faint">—</span>;
  return <span className={`badge ${STATUS_CLASS[value] ?? ""}`} title={title}>{value.replace(/_/g, " ")}</span>;
}

export function Stat({ label, value, hint, accent }: { label: string; value: ReactNode; hint?: ReactNode; accent?: boolean }) {
  return (
    <div className={`stat${accent ? " accent" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint !== undefined && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="spread" style={{ marginBottom: 12 }}>
          {title ? <h2 style={{ margin: 0 }}>{title}</h2> : <span />}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

/** Renders loading / error (with retry) / empty states around real API data. */
export function Load<T>({ state, children, empty, isEmpty }: {
  state: ApiState<T>;
  children: (data: T) => ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
}) {
  if (state.loading && state.data === null) return <div className="state" role="status">Loading…</div>;
  if (state.error) {
    return (
      <div className="state error" role="alert">
        <div>{state.error}</div>
        <button style={{ marginTop: 10 }} onClick={state.reload}>Retry</button>
      </div>
    );
  }
  if (state.data === null) return null;
  if (isEmpty && isEmpty(state.data)) return <div className="state">{empty ?? "Nothing to show yet."}</div>;
  return <>{children(state.data)}</>;
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v === null || v === undefined || v === "" ? <span className="faint">—</span> : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Tabs({ tabs }: { tabs: { id: string; label: ReactNode; content: ReactNode }[] }) {
  const [active, setActive] = useState(tabs[0]?.id);
  const base = useId();
  return (
    <div>
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} role="tab" id={`${base}-${t.id}`} aria-selected={active === t.id}
            aria-controls={`${base}-${t.id}-panel`} onClick={() => setActive(t.id)}>{t.label}</button>
        ))}
      </div>
      {tabs.filter((t) => t.id === active).map((t) => (
        <div key={t.id} role="tabpanel" id={`${base}-${t.id}-panel`} aria-labelledby={`${base}-${t.id}`}>{t.content}</div>
      ))}
    </div>
  );
}

/** Horizontal distribution bars: widths and percentages derived from the given counts only. */
export function Bars({ data, colorFor, limit = 10 }: { data: Record<string, number>; colorFor?: (k: string) => string; limit?: number }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, limit);
  const total = Object.values(data).reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <div className="faint small">No data.</div>;
  return (
    <div className="bars">
      {entries.map(([k, v]) => (
        <div className="bar-row" key={k}>
          <span title={k} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k.replace(/_/g, " ")}</span>
          <div className="bar-track" aria-hidden="true">
            <div className="bar-fill" style={{ width: `${(v / max) * 100}%`, background: colorFor?.(k) }} />
          </div>
          <span className="muted" style={{ textAlign: "right" }}>{v.toLocaleString()} · {pct(v, total)}</span>
        </div>
      ))}
    </div>
  );
}

export const STATUS_COLORS: Record<string, string> = {
  SUCCESS: "#1a7f4b", PARTIAL: "#d08a12", FAILED: "#b42318", UNDER_REVIEW: "#6e44d6",
};

export function TrendChart({ buckets, bucket }: { buckets: { bucket: string; by_status: Record<string, number>; total: number }[]; bucket: string }) {
  if (!buckets.length) return <div className="faint small">No events in this window.</div>;
  const max = Math.max(1, ...buckets.map((b) => b.total));
  const order = ["SUCCESS", "PARTIAL", "UNDER_REVIEW", "FAILED"];
  return (
    <div>
      <div className="trend" role="img" aria-label={`Events per ${bucket}`}>
        {buckets.map((b) => (
          <div key={b.bucket} className="trend-col" style={{ height: `${(b.total / max) * 100}%` }}
            title={`${new Date(b.bucket).toLocaleString()}: ${b.total} event(s) — ${Object.entries(b.by_status).map(([k, v]) => `${k} ${v}`).join(", ")}`}>
            {order.filter((s) => b.by_status[s]).map((s) => (
              <div key={s} style={{ flex: b.by_status[s], background: STATUS_COLORS[s], minHeight: 2 }} />
            ))}
          </div>
        ))}
      </div>
      <div className="spread small faint" style={{ marginTop: 4 }}>
        <span>{new Date(buckets[0].bucket).toLocaleString()}</span>
        <span>per {bucket} · max {max.toLocaleString()}</span>
        <span>{new Date(buckets[buckets.length - 1].bucket).toLocaleString()}</span>
      </div>
    </div>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="code">{JSON.stringify(value, null, 2)}</pre>;
}

export function CursorPager({ hasMore, onOlder, onNewer, canNewer, info }: {
  hasMore: boolean; onOlder: () => void; onNewer: () => void; canNewer: boolean; info?: ReactNode;
}) {
  return (
    <div className="spread" style={{ marginTop: 10 }}>
      <span className="small muted">{info}</span>
      <div className="row">
        <button onClick={onNewer} disabled={!canNewer}>← Newer</button>
        <button onClick={onOlder} disabled={!hasMore}>Older →</button>
      </div>
    </div>
  );
}

export function Pipeline({ steps, current, done }: { steps: string[]; current?: number; done?: number }) {
  return (
    <div className="pipeline" aria-label="Workflow">
      {steps.map((s, i) => (
        <span key={s} className="row" style={{ gap: 6 }}>
          <span className={`step${i === current ? " on" : done !== undefined && i < done ? " done" : ""}`}>{s}</span>
          {i < steps.length - 1 && <span className="arrow" aria-hidden="true">→</span>}
        </span>
      ))}
    </div>
  );
}

/** Identifier with line-break opportunities after "_", "." and "@", so long keys wrap at word boundaries. */
export function Id({ value }: { value: string }) {
  const parts = value.split(/(?<=[_.@])/);
  return <>{parts.map((p, i) => <span key={i}>{p}{i < parts.length - 1 && <wbr />}</span>)}</>;
}
