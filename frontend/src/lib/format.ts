// Display helpers. Percentages are only ever computed from counts the API returned.
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.max(secs, 0)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

export function pct(part: number, total: number): string {
  return total > 0 ? `${((part / total) * 100).toFixed(1)}%` : "—";
}

export function shortHash(hash: string | null | undefined, n = 12): string {
  return hash ? `${hash.slice(0, n)}…` : "—";
}

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString();
}

export function sum(values: Record<string, number> | null | undefined): number {
  return Object.values(values ?? {}).reduce((a, b) => a + b, 0);
}

/** Honest label for who produced a Phase 3/6 suggestion (never implies Claude unless the provider says so). */
export function providerLabel(source: string | null | undefined): string {
  if (!source) return "—";
  if (source === "offline") return "Offline analyzer (deterministic)";
  if (source === "human") return "Human-authored proposal";
  if (source.startsWith("anthropic:")) return `Claude (${source.slice("anthropic:".length)})`;
  if (source.startsWith("deterministic")) {
    return source.includes("+anthropic:") ? "Deterministic engine + Claude suggestions" : "Deterministic learning engine";
  }
  return source;
}

export function toISO(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { timeStyle: "medium" });
}
