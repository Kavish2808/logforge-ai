"""Aggregation of per-event records into the reported metrics. Every number
is computed from the recorded samples; no sample is dropped or trimmed."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from logforge_bench.metrics import latency_summary


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def summarize(records: list[dict[str, Any]], wall_s: float, *, latency_key: str = "latency_ms") -> dict[str, Any]:
    n = len(records)
    statuses = Counter(r.get("status") or ("ERROR" if r.get("error") else "UNKNOWN") for r in records)
    errors = [r for r in records if r.get("error")]
    ok = [r for r in records if not r.get("error")]
    parsed = [r for r in ok if r.get("status") != "FAILED"]
    normalized = [r for r in ok if r.get("status") == "SUCCESS"
                  or (r.get("status") == "UNDER_REVIEW" and r.get("original_status") == "SUCCESS")]
    with_ext = [r for r in ok if (r.get("extension_keys") or 0) > 0]
    raw_known = [r for r in ok if r.get("raw_preserved") is not None]
    sha_known = [r for r in ok if r.get("sha256_verified") is not None]
    malformed = [r for r in records if r.get("malformed")]
    return {
        "throughput": {
            "total_events": n, "wall_seconds": round(wall_s, 4),
            "events_per_sec": round(n / wall_s, 2) if wall_s > 0 else None,
            "events_per_min": round(n / wall_s * 60, 1) if wall_s > 0 else None,
            "successful": statuses.get("SUCCESS", 0), "partial": statuses.get("PARTIAL", 0),
            "failed": statuses.get("FAILED", 0), "under_review": statuses.get("UNDER_REVIEW", 0),
            "harness_errors": len(errors),
        },
        "status_distribution": dict(sorted(statuses.items(), key=lambda kv: str(kv[0]))),
        "latency": latency_summary([r[latency_key] for r in records if r.get(latency_key) is not None]),
        "processing": {
            "parse_success_rate": _rate(len(parsed), len(ok)),
            "normalization_success_rate": _rate(len(normalized), len(ok)),
            "normalization_success_definition": "status SUCCESS, or UNDER_REVIEW whose pre-drift status was SUCCESS",
            "events_with_extensions": len(with_ext),
            "extension_keys_mean": round(sum(r.get("extension_keys") or 0 for r in ok) / len(ok), 2) if ok else None,
            "events_spilled": sum(1 for r in ok if r.get("spilled")),
            "raw_preserved": f"{sum(1 for r in raw_known if r['raw_preserved'])}/{len(raw_known)}" if raw_known else "NOT MEASURED",
            "sha256_verified": f"{sum(1 for r in sha_known if r['sha256_verified'])}/{len(sha_known)}" if sha_known else "NOT MEASURED",
        },
        "malformed": {
            "sent": len(malformed),
            "status_distribution": dict(Counter(r.get("status") or "ERROR" for r in malformed)),
            "by_kind": {k: dict(v) for k, v in _group_status(malformed, "malformed_kind").items()},
            "silently_dropped": sum(1 for r in malformed if not r.get("status") and not r.get("error")),
        },
        "errors_sample": [r["error"] for r in errors[:5]],
    }


def _group_status(records, key) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        out[str(r.get(key))][r.get("status") or "ERROR"] += 1
    return out


def breakdown(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[str(r.get(key))].append(r)
    out = {}
    for name, rs in sorted(groups.items()):
        lat = latency_summary([r["latency_ms"] for r in rs])
        out[name] = {"events": len(rs), "bytes_mean": round(sum(r["bytes"] for r in rs) / len(rs), 1),
                     "p50_ms": lat.get("p50"), "p95_ms": lat.get("p95"), "p99_ms": lat.get("p99"),
                     "mean_ms": lat.get("mean"), "max_ms": lat.get("max"),
                     "status": dict(Counter(r.get("status") or "ERROR" for r in rs))}
    return out
