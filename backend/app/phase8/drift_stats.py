"""Phase 8 Step 4: deterministic statistical drift signals (pure, DB-free).

Phase 5 structural drift is unchanged and stays the first layer. This module
adds a second, statistical layer over the *values* of a few normalized
fields, comparing a baseline window with a current window:

    PSI                       population stability index over top-K categories
    NEW_CATEGORY_SHARE        share of current values never seen in the baseline
    VANISHED_CATEGORY_SHARE   share of baseline values absent from the current window
    NULL_RATE_CHANGE          absolute change of the null rate
    CARDINALITY_RATIO         distinct values, on equal-size samples
    MEDIAN_SHIFT_IQR          |median shift| / baseline IQR (numeric fields only)

Every signal reports what it compared, the threshold, the counts it is based
on and a plain-language explanation. There is no composite score. Standard
library only; identical inputs always give identical output.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

MONITORED_FIELDS: tuple[str, ...] = (
    "event_action", "severity", "network.protocol", "network.dst_port", "network.direction", "user.domain",
)
NUMERIC_FIELDS = frozenset({"network.dst_port"})
EXTENSION_PREFIX = "extensions."

MIN_SAMPLE = 200
TOP_K = 20
PSI_EPSILON = 1e-4
MIN_DISTINCT_FOR_CARDINALITY = 5
MIN_NUMERIC_FOR_MEDIAN = 20
OTHER = "__OTHER__"

# metric -> (threshold, high-severity level). Severity: >= high -> HIGH,
# >= threshold -> MEDIUM, else no finding.
THRESHOLDS: dict[str, tuple[float, float]] = {
    "PSI": (0.25, 0.5),
    "NEW_CATEGORY_SHARE": (0.10, 0.30),
    "VANISHED_CATEGORY_SHARE": (0.10, 0.30),
    "NULL_RATE_CHANGE": (0.10, 0.30),
    "CARDINALITY_RATIO": (2.0, 4.0),
    "MEDIAN_SHIFT_IQR": (1.0, 3.0),
}
METRICS: tuple[str, ...] = tuple(THRESHOLDS)


def severity_for(metric: str, deviation: float) -> str | None:
    threshold, high = THRESHOLDS[metric]
    if deviation >= high:
        return "HIGH"
    if deviation >= threshold:
        return "MEDIUM"
    return None


def reason_for(metric: str) -> str:
    return f"{metric}_AT_OR_ABOVE_THRESHOLD"


def quality_for(baseline_n: int, current_n: int) -> str:
    n = min(baseline_n, current_n)
    return "HIGH" if n >= 1000 else "MEDIUM" if n >= 500 else "LOW"


def normalize_value(value: Any) -> str | None:
    """Categorical form of a value: None / empty / whitespace -> None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def as_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


@dataclass
class Signal:
    field: str
    metric: str
    baseline_value: dict[str, Any]
    current_value: dict[str, Any]
    deviation: float
    threshold: float
    evidence: dict[str, Any]
    explanation: str
    severity: str | None = None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_finding(self) -> bool:
        return self.severity is not None

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "metric": self.metric, "baseline_value": self.baseline_value,
                "current_value": self.current_value, "deviation": self.deviation, "threshold": self.threshold,
                "severity": self.severity, "reason": self.reason, "is_finding": self.is_finding,
                "evidence": self.evidence, "explanation": self.explanation}


def _r(x: float) -> float:
    return round(x, 6)


def top_categories(base: Counter, cur: Counter, k: int = TOP_K) -> list[str]:
    """K categories by combined share (baseline share + current share), ties by value."""
    nb, nc = sum(base.values()) or 1, sum(cur.values()) or 1
    ranked = sorted(set(base) | set(cur), key=lambda v: (-(base[v] / nb + cur[v] / nc), v))
    return ranked[:k]


def bucketed(counts: Counter, categories: list[str]) -> dict[str, int]:
    keep = set(categories)
    out = {c: counts[c] for c in categories}
    other = sum(n for v, n in counts.items() if v not in keep)
    if other:
        out[OTHER] = other
    return out


def psi(base: Counter, cur: Counter, k: int = TOP_K) -> tuple[float, dict[str, int], dict[str, int]]:
    cats = top_categories(base, cur, k)
    b, c = bucketed(base, cats), bucketed(cur, cats)
    keys = cats + ([OTHER] if OTHER in b or OTHER in c else [])
    nb, nc = sum(b.values()), sum(c.values())
    total = 0.0
    for key in keys:
        p = max(b.get(key, 0) / nb, PSI_EPSILON)
        q = max(c.get(key, 0) / nc, PSI_EPSILON)
        total += (q - p) * math.log(q / p)
    return _r(total), b, c


def profile(values: list[str | None], *, numeric: bool, k: int = TOP_K) -> dict[str, Any]:
    """Bounded, JSON-safe distribution summary (used for golden profiles)."""
    non_null = [v for v in values if v is not None]
    counts = Counter(non_null)
    cats = sorted(counts, key=lambda v: (-counts[v], v))[:k]
    out: dict[str, Any] = {"n": len(values), "non_null": len(non_null),
                           "null_rate": _r(1 - len(non_null) / len(values)) if values else None,
                           "distinct": len(counts), "top": bucketed(counts, cats)}
    if numeric:
        nums = [x for x in (as_number(v) for v in non_null) if x is not None]
        if len(nums) >= MIN_NUMERIC_FOR_MEDIAN:
            q1, q2, q3 = statistics.quantiles(nums, n=4, method="inclusive")
            out["numeric"] = {"count": len(nums), "median": q2, "q1": q1, "q3": q3, "iqr": q3 - q1}
    return out


def psi_from_profiles(base: dict[str, Any], cur: dict[str, Any]) -> float | None:
    """PSI between two stored profiles (their bounded top-K buckets)."""
    b, c = Counter(base.get("top") or {}), Counter(cur.get("top") or {})
    if not b or not c:
        return None
    return psi(b, c, k=max(len(b), len(c)))[0]


def compare_field(name: str, base: list[str | None], cur: list[str | None], *, numeric: bool,
                  k: int = TOP_K) -> list[Signal]:
    """All six signals for one field. `base` / `cur` are ordered newest first."""
    signals: list[Signal] = []
    nb_all, nc_all = len(base), len(cur)
    base_nn = [v for v in base if v is not None]
    cur_nn = [v for v in cur if v is not None]
    bc, cc = Counter(base_nn), Counter(cur_nn)
    counts = {"baseline_events": nb_all, "current_events": nc_all,
              "baseline_non_null": len(base_nn), "current_non_null": len(cur_nn)}

    def add(metric, bval, cval, deviation, evidence, explanation):
        threshold = THRESHOLDS[metric][0]
        deviation = _r(deviation)
        sev = severity_for(metric, deviation)
        signals.append(Signal(name, metric, bval, cval, deviation, threshold, {**counts, **evidence}, explanation,
                              severity=sev, reason=reason_for(metric) if sev else None))

    # NULL_RATE_CHANGE (always computable when both windows have events)
    if nb_all and nc_all:
        nrb, nrc = 1 - len(base_nn) / nb_all, 1 - len(cur_nn) / nc_all
        add("NULL_RATE_CHANGE", {"null_rate": _r(nrb)}, {"null_rate": _r(nrc)}, abs(nrc - nrb), {},
            f"'{name}' was empty in {nrb:.1%} of {nb_all} baseline events and {nrc:.1%} of {nc_all} current events "
            f"(absolute change {abs(nrc - nrb):.3f}; threshold {THRESHOLDS['NULL_RATE_CHANGE'][0]}).")

    if not base_nn or not cur_nn:
        return signals  # no value distribution to compare on one side

    value, bb, cb = psi(bc, cc, k)
    add("PSI", {"distribution": bb}, {"distribution": cb}, value, {"top_k": k, "epsilon": PSI_EPSILON},
        f"Population stability index of '{name}' over its top {k} values (rest bucketed as {OTHER}) is {value:.4f} "
        f"(threshold {THRESHOLDS['PSI'][0]}); computed from {len(base_nn)} baseline and {len(cur_nn)} current values.")

    new = {v: cc[v] for v in cc if v not in bc}
    new_share = sum(new.values()) / len(cur_nn)
    new_top = dict(sorted(new.items(), key=lambda kv: (-kv[1], kv[0]))[:k])
    add("NEW_CATEGORY_SHARE", {"categories": len(bc)}, {"new_categories": len(new), "examples": new_top}, new_share,
        {"new_value_events": sum(new.values())},
        f"{new_share:.1%} of current '{name}' values ({sum(new.values())}/{len(cur_nn)}) were never seen in the baseline "
        f"window ({len(new)} new value(s)).")

    gone = {v: bc[v] for v in bc if v not in cc}
    gone_share = sum(gone.values()) / len(base_nn)
    gone_top = dict(sorted(gone.items(), key=lambda kv: (-kv[1], kv[0]))[:k])
    add("VANISHED_CATEGORY_SHARE", {"vanished_categories": len(gone), "examples": gone_top},
        {"categories": len(cc)}, gone_share, {"vanished_value_events": sum(gone.values())},
        f"{gone_share:.1%} of baseline '{name}' values ({sum(gone.values())}/{len(base_nn)}) belong to value(s) absent "
        f"from the current window ({len(gone)} vanished value(s)).")

    m = min(len(base_nn), len(cur_nn))
    db_, dc_ = len(set(base_nn[:m])), len(set(cur_nn[:m]))
    if max(db_, dc_) >= MIN_DISTINCT_FOR_CARDINALITY:
        ratio = dc_ / db_ if db_ else float(dc_)
        spread = max(ratio, 1 / ratio) if ratio else float(db_)
        add("CARDINALITY_RATIO", {"distinct": db_}, {"distinct": dc_, "ratio": _r(ratio)}, spread,
            {"equal_sample_size": m},
            f"On equal samples of {m} most recent values, '{name}' had {db_} distinct values in the baseline and {dc_} "
            f"now (ratio {ratio:.3f}; deviation reported as max(ratio, 1/ratio) = {spread:.3f}, threshold "
            f"{THRESHOLDS['CARDINALITY_RATIO'][0]}).")

    if numeric:
        bn = [x for x in (as_number(v) for v in base_nn) if x is not None]
        cn = [x for x in (as_number(v) for v in cur_nn) if x is not None]
        if len(bn) >= MIN_NUMERIC_FOR_MEDIAN and len(cn) >= MIN_NUMERIC_FOR_MEDIAN:
            q1, bmed, q3 = statistics.quantiles(bn, n=4, method="inclusive")
            cmed = statistics.median(cn)
            iqr = q3 - q1
            scale = iqr if iqr > 0 else 1.0
            shift = abs(cmed - bmed) / scale
            add("MEDIAN_SHIFT_IQR", {"median": bmed, "q1": q1, "q3": q3, "iqr": iqr}, {"median": cmed}, shift,
                {"baseline_numeric": len(bn), "current_numeric": len(cn), "iqr_zero_scaled_by_one": iqr <= 0},
                f"Median of '{name}' moved from {bmed:g} to {cmed:g}: {shift:.3f} baseline IQR(s)"
                + (" (baseline IQR is 0, so the shift is in raw units)" if iqr <= 0 else f" (IQR {iqr:g})")
                + f"; threshold {THRESHOLDS['MEDIAN_SHIFT_IQR'][0]}.")
    return signals


def is_numeric_field(name: str, values: list[str | None]) -> bool:
    if name in NUMERIC_FIELDS:
        return True
    if not name.startswith(EXTENSION_PREFIX):
        return False
    non_null = [v for v in values if v is not None]
    return len(non_null) >= MIN_NUMERIC_FOR_MEDIAN and all(as_number(v) is not None for v in non_null)
