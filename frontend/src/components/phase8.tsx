// Phase 8 building blocks. They render only values the API returned; every
// state-changing action goes through ConfirmAction and is decided server-side
// (RBAC, SOC_ADMIN / note requirements, maker-checker are never re-implemented here).
import { ReactNode, useId, useState } from "react";
import { errorMessage } from "../lib/useApi";

/** Two-step action: the button only opens a confirmation; the API call happens on "Confirm". */
export function ConfirmAction({ label, title, description, danger, noteLabel, noteRequired, onConfirm, disabled }: {
  label: string;
  title?: string;
  description: ReactNode;
  danger?: boolean;
  noteLabel?: string;
  noteRequired?: boolean;
  disabled?: boolean;
  onConfirm: (note: string) => Promise<ReactNode | void>;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: ReactNode } | null>(null);
  const id = useId();
  const blocked = noteRequired && !note.trim();

  const confirm = async () => {
    setBusy(true);
    setResult(null);
    try {
      const text = await onConfirm(note.trim());
      setResult({ ok: true, text: text ?? "Done." });
      setOpen(false);
      setNote("");
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) });  // the backend's reason, verbatim
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="confirm-wrap">
      {!open && (
        <button className={danger ? "danger" : undefined} onClick={() => { setOpen(true); setResult(null); }} disabled={disabled}>
          {label}
        </button>
      )}
      {open && (
        <div className={`confirm ${danger ? "danger" : ""}`} role="group" aria-labelledby={`${id}-t`}>
          <div id={`${id}-t`} className="confirm-title">{title ?? `Confirm: ${label}`}</div>
          <div className="small muted">{description}</div>
          {noteLabel && (
            <label className="field" style={{ marginTop: 8 }}>
              {noteLabel}{noteRequired && " (required)"}
              <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} aria-label={noteLabel} />
            </label>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <button className={danger ? "danger" : "primary"} onClick={confirm} disabled={busy || blocked}>
              {busy ? "Working…" : `Confirm ${label.toLowerCase()}`}
            </button>
            <button className="ghost" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}
      {result && <div className={`notice ${result.ok ? "ok" : "fail"}`} role={result.ok ? "status" : "alert"} style={{ marginTop: 8 }}>{result.text}</div>}
    </div>
  );
}

const VERDICT: Record<string, { cls: string; icon: string; text: string }> = {
  PASSED: { cls: "ok", icon: "✓", text: "PASSED — full coverage, no critical or review-level difference" },
  REVIEW_REQUIRED: { cls: "warn", icon: "!", text: "REVIEW REQUIRED — a human decision is needed before activation" },
  BLOCKED: { cls: "fail", icon: "✕", text: "BLOCKED — circuit breaker tripped; the candidate cannot be activated" },
  RUNNING: { cls: "info", icon: "…", text: "RUNNING — shadow validation in progress" },
};

export function ShadowVerdict({ verdict }: { verdict: string }) {
  const v = VERDICT[verdict] ?? { cls: "info", icon: "?", text: verdict };
  return (
    <div className={`verdict ${v.cls}`} role="status" data-verdict={verdict}>
      <span className="verdict-icon" aria-hidden="true">{v.icon}</span><span>{v.text}</span>
    </div>
  );
}

/** Nine compact lineage stages as a strip; outcome colors match the detailed lineage view. */
export function StageStrip({ stages }: { stages: { stage: string; outcome: string }[] }) {
  return (
    <ol className="stage-strip" aria-label="Compact lineage stages">
      {stages.map((s) => (
        <li key={s.stage} className={`stage s-${s.outcome}`} title={`${s.stage}: ${s.outcome}`}>
          <span className="stage-name">{s.stage.replace(/_/g, " ")}</span>
          <span className="stage-outcome">{s.outcome}</span>
        </li>
      ))}
    </ol>
  );
}

export function Progress({ done, total, label }: { done: number; total: number; label?: string }) {
  const ratio = total > 0 ? Math.min(1, done / total) : 0;
  return (
    <div>
      <div className="spread small"><span>{label ?? "Progress"}</span><span className="muted">{done.toLocaleString()} / {total.toLocaleString()}</span></div>
      <div className="bar-track" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
        aria-label={`${label ?? "Progress"}: ${done} of ${total}`}>
        <div className="bar-fill" style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

/** A yes/no fact reported by the backend (never computed here). */
export function Check({ ok, yes, no }: { ok: boolean | null | undefined; yes: string; no: string }) {
  if (ok === null || ok === undefined) return <span className="faint">—</span>;
  return <span className={`badge ${ok ? "b-ok" : "b-fail"}`}>{ok ? `✓ ${yes}` : `✕ ${no}`}</span>;
}

export function fmtNum(v: number | null | undefined, digits = 4): string {
  return typeof v === "number" ? String(Number(v.toFixed(digits))) : "—";
}
