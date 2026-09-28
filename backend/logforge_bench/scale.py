"""Step 10B — horizontal-scaling evidence helpers (benchmark-only).

- `lb_window`: per-replica request / status / retry counts from the nginx access log for one run window.
- `client_distribution`: the same from the client side (the `X-Upstream` response header).
- `failure_events`: the stop/start timeline written by scripts/bench-scale.sh during failure injection.
- `statelessness_check`: events written through one replica are read back and integrity-verified
  through the load balancer, recording which replica served each read.

Nothing here imports or changes application code paths; reads go through the public HTTP API.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_LINE = re.compile(r'^(?P<msec>\d+\.\d+) (?P<status>\d{3}) "(?P<up>[^"]*)" "(?P<ups>[^"]*)" (?P<rt>[\d.]+) '
                   r'"(?P<urt>[^"]*)" (?P<reqlen>\d+) (?P<sent>\d+) (?P<method>\S+) (?P<uri>\S+)')


def replica_names() -> dict[str, str]:
    """IP -> container name, from the mapping file written by the host wrapper (docker inspect)."""
    path = os.environ.get("LOGFORGE_BENCH_REPLICA_MAP") or "/app/logforge_bench/results/scaling/replica_map.json"
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _name(addr: str, names: dict[str, str]) -> str:
    ip = addr.split(":")[0]
    return names.get(ip, addr)


def lb_window(start_epoch: float, end_epoch: float, log_path: str | None = None) -> dict[str, Any]:
    path = log_path or os.environ.get("LOGFORGE_BENCH_LB_LOG")
    if not path or not Path(path).exists():
        return {"available": False, "reason": "nginx access log not mounted (not a Step 10B run)"}
    names = replica_names()
    by_up: dict[str, Counter] = defaultdict(Counter)
    statuses: Counter = Counter()
    retried = upstream_errors = total = 0
    upstream_time: dict[str, list[float]] = defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LINE.match(line)
            if not m or m["uri"] == "/lb-health":
                continue
            t = float(m["msec"])
            if not (start_epoch <= t <= end_epoch) or not m["uri"].startswith("/api/v1/ingest"):
                continue
            total += 1
            statuses[m["status"]] += 1
            ups = [u.strip() for u in m["up"].split(",") if u.strip() and u.strip() != "-"]
            codes = [c.strip() for c in m["ups"].split(",") if c.strip()]
            if len(ups) > 1:
                retried += 1  # nginx tried more than one replica (connect error on the first)
            for u, c in zip(ups, codes + ["-"] * (len(ups) - len(codes))):
                by_up[_name(u, names)][c] += 1
                if c in ("502", "504") or c == "-":
                    upstream_errors += 1
            for u, rt in zip(ups, (m["urt"] or "").split(",")):
                try:
                    upstream_time[_name(u, names)].append(float(rt.strip()))
                except ValueError:
                    pass
    per = {u: {"attempts": sum(c.values()), "by_upstream_status": dict(c),
               "mean_upstream_s": round(sum(upstream_time[u]) / len(upstream_time[u]), 4) if upstream_time[u] else None}
           for u, c in sorted(by_up.items())}
    counts = [v["attempts"] for v in per.values()]
    return {"available": True, "requests": total, "client_status": dict(statuses), "per_replica": per,
            "requests_retried_on_another_replica": retried, "upstream_attempt_errors": upstream_errors,
            "max_min_ratio": round(max(counts) / min(counts), 3) if counts and min(counts) else None,
            "replicas_seen": len(per)}


def client_distribution(requests: list[dict[str, Any]]) -> dict[str, Any]:
    names = replica_names()
    per: dict[str, Counter] = defaultdict(Counter)
    for r in requests:
        up = r.get("upstream") or "no-response"
        last = up.split(",")[-1].strip()
        per[_name(last, names) if last != "no-response" else last][str(r.get("http_status"))] += 1
    return {k: dict(v) for k, v in sorted(per.items())}


def failure_events(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    path = os.environ.get("LOGFORGE_BENCH_FAILURE_LOG") or "/app/logforge_bench/results/scaling/failure_events.jsonl"
    out = []
    try:
        for line in Path(path).read_text().splitlines():
            e = json.loads(line)
            if start_epoch - 5 <= e["epoch"] <= end_epoch + 5:
                out.append(e)
    except (OSError, ValueError):
        pass
    return out


def _get(host: str, port: int, path: str, timeout: float = 30) -> tuple[int, dict[str, Any] | None, str | None]:
    c = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        up = r.getheader("X-Upstream")
        try:
            return r.status, json.loads(body), up
        except ValueError:
            return r.status, None, up
    except (OSError, socket.timeout, http.client.HTTPException) as exc:
        return 0, {"error": type(exc).__name__}, None
    finally:
        c.close()


def statelessness_check(target_url: str, records: list[dict[str, Any]], *, sample: int = 60) -> dict[str, Any]:
    """Read back events through the load balancer: each event is fetched (GET /api/v1/events/{id}) and
    integrity-verified (GET /api/v1/integrity/events/{id}: stored SHA-256, cold-vault copy, Merkle inclusion
    + anchor) — recording the replica that WROTE it (ingest response) and the replica that SERVED the read."""
    from urllib.parse import urlparse

    u = urlparse(target_url)
    names = replica_names()
    written = [r for r in records if r.get("event_id") and r.get("upstream")]
    by_writer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in written:
        by_writer[_name(r["upstream"].split(",")[-1].strip(), names)].append(r)
    per = max(1, sample // max(1, len(by_writer)))  # an even share of events written by EVERY replica
    picked = []
    for rs in by_writer.values():
        step = max(1, len(rs) // per)
        picked += rs[::step][:per]
    rows = []
    for r in picked:
        writer = _name(r["upstream"].split(",")[-1].strip(), names)
        s1, ev, up1 = _get(u.hostname, u.port or 80, f"/api/v1/events/{r['event_id']}")
        s2, integ, up2 = _get(u.hostname, u.port or 80, f"/api/v1/integrity/events/{r['event_id']}")
        rows.append({"event_id": r["event_id"], "written_by": writer,
                     "read_by": _name((up1 or "?").split(",")[-1].strip(), names),
                     "verified_by": _name((up2 or "?").split(",")[-1].strip(), names),
                     "read_status": s1, "raw_hash_matches": bool(ev) and ev.get("raw_hash") == r.get("raw_hash_sent"),
                     "integrity_status": s2, "integrity_valid": bool(integ) and integ.get("valid") is True,
                     "integrity_verdict": (integ or {}).get("status"),
                     "cold_copy_valid": ((integ or {}).get("cold_copy") or {}).get("valid"),
                     "merkle_inclusion_valid": ((integ or {}).get("merkle") or {}).get("inclusion_valid")})
    cross = [x for x in rows if x["written_by"] != x["verified_by"]]
    return {"sampled": len(rows),
            "writers": dict(Counter(x["written_by"] for x in rows)),
            "verified_on_a_different_replica": len(cross),
            "all_reads_200": all(x["read_status"] == 200 for x in rows),
            "all_integrity_valid": all(x["integrity_valid"] for x in rows),
            "all_raw_hash_match": all(x["raw_hash_matches"] for x in rows),
            "cross_replica_all_valid": all(x["integrity_valid"] and x["read_status"] == 200 for x in cross),
            "rows": rows}
