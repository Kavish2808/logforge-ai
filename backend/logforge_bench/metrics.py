"""Measurement primitives: latency summaries, process/cgroup resource
sampling (from /proc and /sys/fs/cgroup — no third-party dependency) and
PostgreSQL activity counters. Anything that cannot be read is reported as
"NOT MEASURED" with the reason, never estimated."""
from __future__ import annotations

import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

NOT_MEASURED = "NOT MEASURED"
_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def percentile(sorted_values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default 'linear')."""
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def latency_summary(values_ms: list[float]) -> dict[str, Any]:
    if not values_ms:
        return {"n": 0}
    s = sorted(values_ms)
    r = lambda x: None if x is None else round(x, 3)  # noqa: E731 — display precision only; raw samples are kept
    return {"n": len(s), "min": r(s[0]), "p50": r(percentile(s, 0.50)), "p95": r(percentile(s, 0.95)),
            "p99": r(percentile(s, 0.99)), "max": r(s[-1]), "mean": r(statistics.fmean(s)),
            "stdev": r(statistics.pstdev(s)) if len(s) > 1 else 0.0, "unit": "ms",
            "percentile_method": "linear interpolation between closest ranks"}


# --------------------------------------------------------------------------
# Process / container resources
# --------------------------------------------------------------------------


def proc_cpu_seconds(pid: int | str = "self") -> float | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / _CLK_TCK  # utime + stime
    except (OSError, IndexError, ValueError):
        return None


def proc_rss_mb(pid: int | str = "self") -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError):
        return None
    return None


def cgroup_memory_mb() -> float | None:
    """Memory charged to this container's cgroup (v2, then v1)."""
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            return round(int(Path(path).read_text().strip()) / 1024 / 1024, 2)
        except (OSError, ValueError):
            continue
    return None


class ResourceSampler:
    """Samples CPU% and RSS of the named processes (and the container cgroup)
    every `interval` seconds in a background thread."""

    def __init__(self, pids: dict[str, int | str], interval: float = 1.0):
        self.pids = pids
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def _sample(self, prev: dict[str, tuple[float, float | None]]) -> None:
        now = time.perf_counter()
        row: dict[str, Any] = {"t": round(now - self._t0, 3), "cgroup_mb": cgroup_memory_mb()}
        for name, pid in self.pids.items():
            cpu = proc_cpu_seconds(pid)
            last_t, last_cpu = prev.get(name, (now, cpu))
            dt = now - last_t
            row[f"{name}_rss_mb"] = proc_rss_mb(pid)
            row[f"{name}_cpu_pct"] = (round((cpu - last_cpu) / dt * 100, 1)
                                      if cpu is not None and last_cpu is not None and dt > 0 else None)
            prev[name] = (now, cpu)
        self.samples.append(row)

    def _run(self) -> None:
        prev: dict[str, tuple[float, float | None]] = {}
        for name, pid in self.pids.items():
            prev[name] = (time.perf_counter(), proc_cpu_seconds(pid))
        while not self._stop.wait(self.interval):
            self._sample(prev)

    def start(self) -> "ResourceSampler":
        self._t0 = time.perf_counter()
        self._cpu0 = {n: proc_cpu_seconds(p) for n, p in self.pids.items()}
        self._thread = threading.Thread(target=self._run, name="bench-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        wall = time.perf_counter() - self._t0
        out: dict[str, Any] = {"interval_s": self.interval, "samples": len(self.samples), "wall_s": round(wall, 3)}
        for name, pid in self.pids.items():
            cpu1 = proc_cpu_seconds(pid)
            cpu0 = self._cpu0.get(name)
            rss = [s[f"{name}_rss_mb"] for s in self.samples if s.get(f"{name}_rss_mb") is not None]
            out[name] = {
                "cpu_seconds": round(cpu1 - cpu0, 3) if cpu1 is not None and cpu0 is not None else NOT_MEASURED,
                "cpu_pct_of_one_core_avg": (round((cpu1 - cpu0) / wall * 100, 1)
                                            if cpu1 is not None and cpu0 is not None and wall > 0 else NOT_MEASURED),
                "rss_mb_start": rss[0] if rss else proc_rss_mb(pid) or NOT_MEASURED,
                "rss_mb_max": max(rss) if rss else NOT_MEASURED,
                "rss_mb_end": rss[-1] if rss else NOT_MEASURED,
            }
        cg = [s["cgroup_mb"] for s in self.samples if s.get("cgroup_mb") is not None]
        out["container_cgroup_mb"] = ({"start": cg[0], "max": max(cg), "end": cg[-1],
                                       "note": "backend container cgroup (benchmark client + server + page cache)"}
                                      if cg else NOT_MEASURED)
        return out


# --------------------------------------------------------------------------
# PostgreSQL activity
# --------------------------------------------------------------------------

_PG_COUNTERS = ("xact_commit", "xact_rollback", "tup_inserted", "tup_updated", "tup_deleted", "tup_fetched",
                "tup_returned", "blks_read", "blks_hit", "deadlocks", "conflicts", "temp_bytes")
_EVENT_TABLES = ("events", "event_raw_storage", "event_extension_overflow", "event_lineage_compact",
                 "event_revisions", "overflow_signatures", "source_baselines", "source_baseline_history",
                 "evidence_batches", "evidence_batch_members")


def pg_snapshot(engine) -> dict[str, Any]:
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        row = conn.execute(text(f"SELECT {', '.join(_PG_COUNTERS)}, numbackends FROM pg_stat_database "
                                "WHERE datname = current_database()")).mappings().one()
        sizes = {}
        for t in _EVENT_TABLES:
            sizes[t] = conn.execute(text("SELECT COALESCE(pg_total_relation_size(to_regclass(:t)), 0)"), {"t": t}).scalar()
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                  for t in ("events", "event_raw_storage", "event_extension_overflow", "event_lineage_compact")}
        db_bytes = conn.execute(text("SELECT pg_database_size(current_database())")).scalar()
        conns = conn.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar()
        cluster: dict[str, Any] = {}
        for name, sql in (("wal", f"SELECT {', '.join(_WAL)} FROM pg_stat_wal"),
                          ("bgwriter", f"SELECT {', '.join(_BGW)} FROM pg_stat_bgwriter")):
            try:
                cluster[name] = {k: float(v) for k, v in conn.execute(text(sql)).mappings().one().items()}
            except Exception as exc:  # noqa: BLE001 — version-dependent views: recorded, not fatal
                cluster[name] = {"error": f"{type(exc).__name__}"}
                conn.rollback()
    return {"counters": {k: int(row[k]) for k in _PG_COUNTERS}, "numbackends": int(row["numbackends"]),
            "connections": int(conns), "table_bytes": sizes, "row_counts": counts, "database_bytes": int(db_bytes),
            "cluster": cluster}


# Cluster-wide (whole PostgreSQL server, including the dev stack's own database).
_WAL = ("wal_records", "wal_fpi", "wal_bytes", "wal_buffers_full", "wal_write", "wal_sync", "wal_write_time",
        "wal_sync_time")
_BGW = ("checkpoints_timed", "checkpoints_req", "checkpoint_write_time", "checkpoint_sync_time",
        "buffers_checkpoint", "buffers_clean", "buffers_backend", "buffers_backend_fsync", "buffers_alloc")


def _cluster_delta(before: dict[str, Any], after: dict[str, Any], events: int, wall_s: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("wal", "bgwriter"):
        b, a = (before.get("cluster") or {}).get(name) or {}, (after.get("cluster") or {}).get(name) or {}
        if "error" in a or "error" in b or not a:
            out[name] = NOT_MEASURED
            continue
        out[name] = {k: round(a[k] - b[k], 3) for k in a}
    wal = out.get("wal")
    if isinstance(wal, dict):
        out["wal_bytes_per_event"] = round(wal["wal_bytes"] / events, 1) if events else None
        out["wal_records_per_event"] = round(wal["wal_records"] / events, 2) if events else None
        out["wal_syncs_per_event"] = round(wal["wal_sync"] / events, 3) if events else None
        # wal_*_time is milliseconds, recorded only by sessions with track_wal_io_timing on (the
        # benchmark database sets it), so it times the benchmark's own WAL writes and fsyncs.
        out["wal_fsync_ms_avg"] = round(wal["wal_sync_time"] / wal["wal_sync"], 3) if wal["wal_sync"] and wal["wal_sync_time"] else None
        out["wal_write_ms_avg"] = round(wal["wal_write_time"] / wal["wal_write"], 3) if wal["wal_write"] and wal["wal_write_time"] else None
        out["wal_fsync_ms_per_event"] = round(wal["wal_sync_time"] / events, 3) if events and wal["wal_sync_time"] else None
        if wall_s:
            out["wal_mb_per_sec"] = round(wal["wal_bytes"] / wall_s / 1e6, 3)
            out["wal_syncs_per_sec"] = round(wal["wal_sync"] / wall_s, 1)
    out["scope"] = ("CLUSTER-WIDE: pg_stat_wal / pg_stat_bgwriter include the dev stack's own database on the same "
                    "server (idle apart from its 60 s scheduler tick); wal_*_time is 0 unless track_wal_io_timing is on")
    return out


def pg_delta(before: dict[str, Any], after: dict[str, Any], events: int, wall_s: float | None = None) -> dict[str, Any]:
    d = {k: after["counters"][k] - before["counters"][k] for k in _PG_COUNTERS}
    per = (lambda v: round(v / events, 3) if events else None)  # noqa: E731
    rate = (lambda v: round(v / wall_s, 1) if wall_s else None)  # noqa: E731
    table_growth = {t: after["table_bytes"][t] - before["table_bytes"][t] for t in _EVENT_TABLES}
    return {
        "counters_delta": d,
        "per_event": {"commits": per(d["xact_commit"]), "rollbacks": per(d["xact_rollback"]),
                      "rows_inserted": per(d["tup_inserted"]), "rows_updated": per(d["tup_updated"])},
        "per_second": {"commits": rate(d["xact_commit"]), "rows_inserted": rate(d["tup_inserted"]),
                       "rows_updated": rate(d["tup_updated"]), "rows_fetched": rate(d["tup_fetched"])},
        "cluster": _cluster_delta(before, after, events, wall_s),
        "row_counts_after": after["row_counts"],
        "rows_added": {t: after["row_counts"][t] - before["row_counts"][t] for t in after["row_counts"]},
        "table_bytes_growth": table_growth,
        "bytes_per_event_all_event_tables": per(sum(table_growth.values())),
        "database_bytes_growth": after["database_bytes"] - before["database_bytes"],
        "connections_during_after": after["connections"],
        "note": ("pg_stat_database counters cover the whole benchmark database (including the harness's own "
                 "verification queries, which are read-only) and are flushed asynchronously by PostgreSQL; "
                 "relation sizes include indexes/TOAST and grow in pages, so small runs are coarse."),
    }


class PgActivitySampler:
    """Samples pg_stat_activity of the benchmark database every `interval`
    seconds on its own connection: how many backends are active on CPU,
    waiting (by wait_event_type:wait_event), idle, or idle in transaction.
    Counts are backend-samples, i.e. a time-weighted picture of where
    PostgreSQL backends spend their time during the run."""

    _SQL = ("SELECT state, wait_event_type, wait_event, count(*) AS n FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend' "
            "GROUP BY 1, 2, 3")

    def __init__(self, engine, interval: float = 0.5):
        self.engine = engine
        self.interval = interval
        self.ticks = 0
        self.counts: dict[str, int] = {}
        self.max_active = 0
        self.max_connections = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def _run(self) -> None:
        from sqlalchemy import text

        try:
            with self.engine.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                while not self._stop.wait(self.interval):
                    rows = conn.execute(text(self._SQL)).all()
                    self.ticks += 1
                    active = total = 0
                    for state, wtype, wevent, n in rows:
                        total += n
                        if state == "active":
                            active += n
                            key = "active: on CPU (no wait event)" if not wtype else f"active: waiting {wtype}:{wevent}"
                        else:
                            key = f"{state or 'unknown'}" + (f" ({wtype}:{wevent})" if wtype and state != "idle" else "")
                        self.counts[key] = self.counts.get(key, 0) + n
                    self.max_active = max(self.max_active, active)
                    self.max_connections = max(self.max_connections, total)
        except Exception as exc:  # noqa: BLE001 — sampling failure is recorded, never fatal
            self.error = f"{type(exc).__name__}: {str(exc)[:200]}"

    def start(self) -> "PgActivitySampler":
        self._thread = threading.Thread(target=self._run, name="bench-pg-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        total = sum(self.counts.values())
        active = {k: v for k, v in self.counts.items() if k.startswith("active")}
        active_total = sum(active.values())
        return {
            "interval_s": self.interval, "ticks": self.ticks, "error": self.error,
            "backend_samples": total, "max_client_connections": self.max_connections,
            "max_simultaneously_active": self.max_active,
            "mean_active_backends": round(active_total / self.ticks, 3) if self.ticks else None,
            "by_state_and_wait": dict(sorted(self.counts.items(), key=lambda kv: -kv[1])),
            "active_share": {k: round(v / active_total, 4) for k, v in sorted(active.items(), key=lambda kv: -kv[1])}
            if active_total else {},
            "note": "backend-samples of client backends in the benchmark database (the sampler's own connection "
                    "excluded); 'idle' = connection open but the application is not using it at that instant",
        }
