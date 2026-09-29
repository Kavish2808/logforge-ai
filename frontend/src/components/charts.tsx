// SVG charts drawn only from the counts they are given (no sampling, no invented points).
import { STATUS_COLORS } from "./ui";

type Bucket = { bucket: string; by_status: Record<string, number>; total: number };

const ORDER = ["SUCCESS", "PARTIAL", "UNDER_REVIEW", "FAILED"];

/** Stacked area chart of events per bucket, split by processing status. */
export function AreaChart({ buckets, bucket }: { buckets: Bucket[]; bucket: string }) {
  if (!buckets.length) return null;
  const W = 640, H = 170, P = 4;
  const max = Math.max(1, ...buckets.map((b) => b.total));
  const n = buckets.length;
  const x = (i: number) => (n === 1 ? W / 2 : P + (i * (W - 2 * P)) / (n - 1));
  const y = (v: number) => H - (v / max) * (H - 12);
  const layers: { key: string; d: string }[] = [];
  const base = new Array(n).fill(0);
  for (const key of ORDER) {
    const top = buckets.map((b, i) => base[i] + (b.by_status[key] ?? 0));
    if (top.every((v, i) => v === base[i])) continue;
    const upper = top.map((v, i) => `${x(i)},${y(v)}`);
    const lower = base.map((v, i) => `${x(i)},${y(v)}`).reverse();
    const d = n === 1
      ? `M${x(0) - 30},${y(top[0])} L${x(0) + 30},${y(top[0])} L${x(0) + 30},${y(base[0])} L${x(0) - 30},${y(base[0])} Z`
      : `M${upper.join(" L")} L${lower.join(" L")} Z`;
    layers.push({ key, d });
    top.forEach((v, i) => { base[i] = v; });
  }
  const totalLine = buckets.map((b, i) => `${i ? "L" : "M"}${x(i)},${y(b.total)}`).join(" ");
  return (
    <div>
      <svg className="area-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`Events per ${bucket}, stacked by status; peak ${max.toLocaleString()} per ${bucket}`}>
        <defs>
          {layers.map((l) => (
            <linearGradient key={l.key} id={`ag-${l.key}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={STATUS_COLORS[l.key]} stopOpacity="0.55" />
              <stop offset="100%" stopColor={STATUS_COLORS[l.key]} stopOpacity="0.08" />
            </linearGradient>
          ))}
        </defs>
        {[0.25, 0.5, 0.75].map((f) => <line key={f} className="grid-line" x1="0" x2={W} y1={H - f * (H - 12)} y2={H - f * (H - 12)} vectorEffect="non-scaling-stroke" />)}
        {layers.map((l) => <path key={l.key} d={l.d} fill={`url(#ag-${l.key})`} stroke={STATUS_COLORS[l.key]} strokeOpacity="0.5" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
        {n > 1 && <path d={totalLine} fill="none" stroke="#9db6ff" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />}
        {buckets.map((b, i) => (
          <rect key={b.bucket} x={x(i) - (W / n) / 2} y="0" width={W / n} height={H} fill="transparent">
            <title>{`${new Date(b.bucket).toLocaleString()}: ${b.total} event(s) — ${Object.entries(b.by_status).map(([k, v]) => `${k} ${v}`).join(", ")}`}</title>
          </rect>
        ))}
      </svg>
      <div className="spread small faint" style={{ marginTop: 6 }}>
        <span>{new Date(buckets[0].bucket).toLocaleString()}</span>
        <span>per {bucket} · peak {max.toLocaleString()}</span>
        <span>{new Date(buckets[n - 1].bucket).toLocaleString()}</span>
      </div>
    </div>
  );
}

const PALETTE = ["#4f7cff", "#22d3ee", "#9b8cff", "#3ddc97", "#f5b945", "#ff8a8a", "#5fb3ff", "#9aa8bf"];

/** Donut of a count distribution; the legend shows the exact counts and shares. */
export function Donut({ data, label, limit = 6 }: { data: Record<string, number>; label: string; limit?: number }) {
  const sorted = Object.entries(data).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const head = sorted.slice(0, limit);
  const rest = sorted.slice(limit).reduce((a, [, v]) => a + v, 0);
  const parts = rest ? [...head, ["other", rest] as [string, number]] : head;
  const total = parts.reduce((a, [, v]) => a + v, 0);
  if (!total) return null;
  const R = 52, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" width="150" height="150" role="img" aria-label={`${label}: ${parts.map(([k, v]) => `${k} ${v}`).join(", ")}`}>
        <circle cx="70" cy="70" r={R} fill="none" stroke="#1a2740" strokeWidth="16" />
        {parts.map(([k, v], i) => {
          const len = (v / total) * C;
          const el = (
            <circle key={k} cx="70" cy="70" r={R} fill="none" stroke={PALETTE[i % PALETTE.length]} strokeWidth="16"
              strokeDasharray={`${Math.max(len - 1.5, 0.5)} ${C}`} strokeDashoffset={-offset} transform="rotate(-90 70 70)">
              <title>{`${k}: ${v.toLocaleString()} (${((v / total) * 100).toFixed(1)}%)`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
        <text x="70" y="68" textAnchor="middle" className="donut-center">{total.toLocaleString()}</text>
        <text x="70" y="86" textAnchor="middle" className="donut-sub">{label}</text>
      </svg>
      <div className="legend">
        {parts.map(([k, v], i) => (
          <span key={k}><span className="row" style={{ gap: 7, flexWrap: "nowrap", minWidth: 0 }}>
            <i style={{ background: PALETTE[i % PALETTE.length] }} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k}</span></span>
            <b>{v.toLocaleString()} <span className="faint small">· {((v / total) * 100).toFixed(1)}%</span></b></span>
        ))}
      </div>
    </div>
  );
}

/** Tiny line of the given series (e.g. events per bucket). Renders nothing for fewer than two points. */
export function Sparkline({ values, color = "#4f7cff" }: { values: number[]; color?: string }) {
  if (values.length < 2) return null;
  const W = 200, H = 34, max = Math.max(1, ...values);
  const pts = values.map((v, i) => `${(i * W) / (values.length - 1)},${H - (v / max) * (H - 4) - 2}`);
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={`M${pts.join(" L")} L${W},${H} L0,${H} Z`} fill={color} fillOpacity="0.12" />
      <path d={`M${pts.join(" L")}`} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
