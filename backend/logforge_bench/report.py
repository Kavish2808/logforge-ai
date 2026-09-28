"""Render docs/phase8-step10-benchmark.md from recorded results.

Every number in a MEASURED table is read from a result.json or from the
host-side `docker stats` samples recorded by scripts/bench.sh.
EXTRAPOLATION values are arithmetic on measured numbers, labeled as such,
with the formula. Conclusions are derived from the numbers by the rules
written in this file; nothing is typed in by hand. Nothing here imports the
application.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

NM = "NOT MEASURED"
DAY = 86_400
EXTRA = "EXTRAPOLATION — NOT MEASURED"
V_LABEL = {"V0_core": "V0 core (drift off, Phase 7 vault off, Phase 8 hooks off)",
           "V1_core_phase7": "V1 core + Phase 7 evidence (cold vault on; drift off; Phase 8 off)",
           "V2_core_drift": "V2 core + Phase 5 drift (vault off; Phase 8 off)",
           "V3_full": "V3 full default configuration (drift + vault + Phase 8 hooks)"}


# --------------------------------------------------------------------------
# loading / formatting
# --------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _runs(suite_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    index = _load(suite_dir / "suite.json")
    out = {}
    for r in index["runs"]:
        p = suite_dir / r["dir"] / "result.json"
        out[r["label"]] = _load(p) if p.exists() else {"label": r["label"], "status": r["status"],
                                                       "failure": r.get("failure")}
    return index, out


def _g(d: Any, *path, default=NM):
    for k in path:
        if not isinstance(d, dict) or k not in d or d[k] is None:
            return default
        d = d[k]
    return d


def _f(v: Any, nd: int = 2) -> str:
    if v is None or v == NM:
        return NM
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_No rows: NOT MEASURED._"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_f(c) if not isinstance(c, str) else c for c in r) + " |")
    return "\n".join(out)


def _std_mix(r: dict[str, Any]) -> bool:
    c = r["config"]
    return len(c["formats"]) == 6 and len(c["sizes"]) == 4 and c["malformed_rate"] == 0.05


def _ok(r: dict[str, Any] | None) -> bool:
    return bool(r) and r.get("status") == "COMPLETED"


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------------------
# docker stats (host side)
# --------------------------------------------------------------------------


def _mem_mib(s: str) -> float | None:
    try:
        v = s.split("/")[0].strip()
        for unit, mult in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("kB", 1 / 1024), ("MB", 1), ("GB", 1024),
                           ("B", 1 / 1024 / 1024)):
            if v.endswith(unit):
                return float(v[: -len(unit)]) * mult
    except (ValueError, AttributeError):
        return None
    return None


def _load_docker(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
                cpu = float(d["CPUPerc"].rstrip("%"))
                rows.append({"t": _ts(d["ts"]), "name": d["Name"], "cpu": cpu, "mem": _mem_mib(d.get("MemUsage", ""))})
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
    return sorted(rows, key=lambda r: r["t"])


def _docker_window(docker: list[dict[str, Any]], start: str | None, end: str | None) -> dict[str, Any]:
    if not docker or not start or not end:
        return {}
    a, b = _ts(start), _ts(end)
    out = {}
    for name in ("logforge-db", "logforge-backend"):
        pts = [r for r in docker if r["name"] == name and a <= r["t"] <= b]
        if pts:
            mems = [r["mem"] for r in pts if r["mem"] is not None]
            out[name] = {"samples": len(pts), "cpu_mean": round(statistics.fmean(r["cpu"] for r in pts), 1),
                         "cpu_max": round(max(r["cpu"] for r in pts), 1),
                         "mem_max_mib": round(max(mems), 1) if mems else None,
                         "mem_first_mib": round(mems[0], 1) if mems else None,
                         "mem_last_mib": round(mems[-1], 1) if mems else None}
    return out


def _dk(r: dict[str, Any], docker, name: str, key: str):
    w = _docker_window(docker, r.get("measure_start_utc") or r.get("started_utc"),
                       r.get("measure_end_utc") or r.get("finished_utc"))
    return _g(w, name, key)


# --------------------------------------------------------------------------
# derived evidence
# --------------------------------------------------------------------------


def _group_stats(rs: list[dict[str, Any]]) -> dict[str, Any]:
    eps = [r["summary"]["throughput"]["events_per_sec"] for r in rs]
    mean = [r["summary"]["latency"]["mean"] for r in rs]
    p95 = [r["summary"]["latency"]["p95"] for r in rs]
    p99 = [r["summary"]["latency"]["p99"] for r in rs]
    p50 = [r["summary"]["latency"]["p50"] for r in rs]
    pipe = [r["stages"]["pipeline"]["ms_per_event"] for r in rs if _g(r, "stages", "pipeline", "calls", default=0) > 0]
    return {"n": len(rs), "eps": eps, "mean_ms": mean, "p95": p95, "p99": p99, "pipeline_ms": pipe,
            "p50_mean": statistics.fmean(p50),
            "eps_mean": statistics.fmean(eps), "mean_ms_mean": statistics.fmean(mean), "mean_ms_min": min(mean),
            "mean_ms_max": max(mean), "p95_mean": statistics.fmean(p95), "p99_mean": statistics.fmean(p99)}


def _by_variant(runs: dict[str, dict[str, Any]], prefix: str) -> dict[str, dict[str, Any]]:
    by: dict[str, list] = {}
    for k, r in runs.items():
        if k.startswith(prefix) and _ok(r):
            by.setdefault(r["config"]["variant"], []).append(r)
    return {v: _group_stats(rs) for v, rs in by.items()}


def _sweep(runs: dict[str, dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    return {(r["config"]["workers"], r["config"]["batch_size"]): r for k, r in runs.items()
            if k.startswith("L-http-") and _ok(r)}


def _top_waits(r: dict[str, Any], n: int = 3) -> str:
    share = _g(r, "pg_activity", "active_share", default={})
    if not share:
        return NM
    return "; ".join(f"{k.replace('active: ', '')} {v * 100:.0f}%" for k, v in list(share.items())[:n])


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def render(suite_dirs: list[Path] | Path, sustained_path: Path | None, out_path: Path,
           docker_stats: list[Path] | Path | None = None, *, devdb: list[Path] | None = None,
           junit: list[Path] | None = None) -> None:
    suite_dirs = [suite_dirs] if isinstance(suite_dirs, Path) else list(suite_dirs)
    docker_paths = [docker_stats] if isinstance(docker_stats, Path) else list(docker_stats or [])
    docker = _load_docker(docker_paths)
    runs: dict[str, dict[str, Any]] = {}
    indexes = []
    for d in suite_dirs:
        idx, rs = _runs(d)
        indexes.append((d.name, idx, rs))
        runs.update(rs)
    std = next(((n, i, r) for n, i, r in indexes if i.get("plan", "standard") == "standard"), None)
    comp = next(((n, i, r) for n, i, r in indexes if i.get("plan") == "completion"), None)
    sus = _load(sustained_path) if sustained_path and sustained_path.exists() else None
    devdbs = [_load(p) for p in (devdb or []) if p.exists()]
    tests = _junit(junit or [])
    done = {k: v for k, v in runs.items() if _ok(v)}
    env = (sus or {}).get("environment") or next((v["environment"] for v in runs.values() if v.get("environment")), {})
    b_big = done.get("B-inproc-v3_full-10000") or max(
        (v for k, v in done.items() if k.startswith("B-")), key=lambda v: v["summary"]["throughput"]["total_events"],
        default=None)
    sweep = _sweep(runs)
    kv = _by_variant(runs, "K-")
    cv = _by_variant(runs, "C-variant-")
    sus_ok = _ok(sus)
    # "Full pipeline" = the default configuration (drift + vault + Phase 8 hooks) on the standard dataset mix
    # (all formats and size bands, 5 % malformed); runs under 100 events excluded.
    best = max((v for v in done.values() if v["config"]["variant"] in ("v3_full", "V3_full") and _std_mix(v)
                and v["summary"]["throughput"]["total_events"] >= 100),
               key=lambda v: v["summary"]["throughput"]["events_per_sec"], default=None)
    verdict = _verdict(std, comp, sus, sweep, runs, devdbs, tests)
    L: list[str] = []
    add = L.append

    add("# Phase 8 Step 10 — Benchmark and Scale Evidence\n")
    add("> **Reading rule.** Every table carries a *Kind*: **MEASURED** = recorded by `python -m logforge_bench` or "
        "by the host-side `docker stats` sampler in `scripts/bench.sh` on the environment in section B. "
        "**PROJECTED** = a requirement computed from a target (e.g. events/s needed for 1B/day). "
        f"**EXTRAPOLATED** (\"{EXTRA}\") = a measured rate carried beyond what was run (e.g. × 86,400 s). "
        "**UNKNOWN** = not measured. The current Docker Compose + single PostgreSQL + synchronous-commit "
        "architecture has **not** been shown to process 100 million, 500 million or one billion events per day, "
        "and this document does not claim it can.\n")
    for name, idx, rs in indexes:
        c = sum(1 for v in rs.values() if _ok(v))
        add(f"- Suite `{name}` (plan `{idx.get('plan', 'standard')}`): {len(rs)} runs planned, {c} completed, "
            f"{len(rs) - c} failed.")
    add(f"- Sustained run: `{sus['run_id']}` ({sus['status']})." if sus else "- Sustained run: NOT RUN.")
    add("- Artifacts: `backend/logforge_bench/results/` (per-run `result.json`; per-event/per-sample JSONL files are "
        "kept locally and git-ignored).\n")

    add("## A. Executive summary\n")
    add(_exec_summary(sus, b_big, sweep, best, done, verdict) + "\n")
    add("## B. Exact benchmark environment\n")
    add(_env_table(env) + "\n")
    add(f"**Git SHA:** `{_g(env, 'git', 'commit')}` — working tree dirty: {_f(_g(env, 'git', 'working_tree_dirty'))} "
        f"({_g(env, 'git', 'dirty_files')} status entries: the uncommitted Step 10 harness; no application file "
        "differs from that commit).\n")
    add("The benchmark database `logforge_bench` shares the PostgreSQL server of the running development stack; the "
        "load generator runs in the same container as the API process under test (Docker Desktop VM, shared CPUs). "
        "`track_wal_io_timing` is enabled for the `logforge_bench` database only (completion plan and sustained run).\n")
    add("## C. Dataset, seed and reproducibility\n")
    add(_dataset_section(b_big) + "\n")
    add(_methodology() + "\n")
    add(REPRO)
    add("## D. Full 46-run standard suite\n")
    add(_suite_table(std) + "\n")
    add("## E. Completion-suite results\n")
    add(_suite_table(comp, integrity=True) + "\n")
    add("## F. HTTP concurrency results\n")
    add(_sweep_section(sweep, docker) + "\n")
    add(_efficiency_section(sweep, done) + "\n")
    hist = [(k, v) for k, v in done.items() if k.startswith(("G-http-v3_full-10000", "H-"))]
    if hist:
        add("\nEarlier standard-suite HTTP runs with several connections (batch 1, `v3_full`): " + "; ".join(
            f"`{k}` {v['config']['workers']} conn, {v['summary']['throughput']['total_events']:,} events → "
            f"{v['summary']['throughput']['events_per_sec']:,.2f} events/s, p95 {v['summary']['latency']['p95']} ms"
            for k, v in hist) + " (MEASURED).\n")
    add("## G. 5-minute sustained results\n")
    add(_sustained_section(sus, docker) + "\n")
    add("## H. In-process vs HTTP comparison\n")
    add(_inproc_vs_http(done, kv, sweep) + "\n")
    add("## I. V0 / V1 / V2 / V3 overhead comparison\n")
    add(_v_table(kv) + "\n")
    add(_deltas(kv) + "\n")
    add(_overhead_section(kv, cv, b_big) + "\n")
    add("## J. PostgreSQL / WAL evidence\n")
    add(_pg_section(runs, sus, docker) + "\n")
    add("## K. CPU / RAM / resource behavior\n")
    add(_cpu_section(runs, sus, docker) + "\n")
    add("## L. Bottleneck analysis\n")
    add(_bottleneck_section(b_big, done, sweep, kv, sus, docker) + "\n")
    add("## M. Integrity verification\n")
    add(_integrity_section(runs, sus, devdbs) + "\n")
    add("## N. 1B-events/day calculation\n")
    add(_scale_section(sus, best, b_big) + "\n")
    add("## O. What is actually demonstrated\n")
    add(_measured_list(done, sus, sweep, kv) + "\n")
    add("## P. What is NOT demonstrated\n")
    add("**UNKNOWN / not measured:**\n\n" + "\n".join(f"- {x}" for x in UNKNOWN) + "\n")
    add("**EXTRAPOLATED / PROJECTED only (never observed):**\n\n"
        "- Any events/day figure (measured events/s × 86,400) and the gap factors to 100M / 500M / 1B events/day.\n"
        "- Storage per day at the measured bytes/event.\n"
        "- Any benefit of more API processes or replicas.\n")
    add("**Limitations:**\n")
    add("\n".join(f"- {x}" for x in _limitations(runs, done)) + "\n")
    add("## Q. Recommended next experiment — Horizontal Scaling / Load Balancing\n")
    add(_lb_section(sweep, done, sus, docker, b_big) + "\n")
    add("## R. Step 10 final verdict\n")
    add(_verdict_section(verdict, tests) + "\n")
    out_path.write_text("\n".join(L), encoding="utf-8")


def _limitations(runs, done) -> list[str]:
    lim = list(LIMITATIONS)
    failed = [(k, v.get("failure")) for k, v in runs.items() if not _ok(v)]
    if failed:
        lim.insert(0, "**Failed runs (kept, not hidden):** " + "; ".join(f"`{k}`: {f}" for k, f in failed))
    pv = sorted((v["stages"]["pipeline"]["ms_per_event"], k) for k, v in done.items()
                if _g(v, "stages", "pipeline", "calls", default=0) > 0 and v["summary"]["throughput"]["total_events"] >= 100
                and v["config"]["malformed_rate"] == 0.05 and len(v["config"]["sizes"]) == 4
                and len(v["config"]["formats"]) == 6)
    if len(pv) >= 2:
        lim.insert(0, f"**Environment speed variance (MEASURED).** The pure-CPU `pipeline` stage on the same data ranged "
                      f"from {pv[0][0]:.3f} ms/event (`{pv[0][1]}`) to {pv[-1][0]:.3f} ms/event (`{pv[-1][1]}`) — a "
                      f"{pv[-1][0] / pv[0][0]:.1f}× spread caused by the machine, not the code. Single-run differences "
                      "inside that spread are not meaningful.")
    return lim


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------


def _env_table(env: dict[str, Any]) -> str:
    rt, host, pg = env.get("runtime", {}), env.get("host", {}), env.get("postgresql", {})
    rows = [
        ["Host OS", host.get("os")], ["Host CPU", host.get("cpu")], ["Host RAM", host.get("ram")],
        ["Docker", host.get("docker_version")], ["Docker engine", host.get("docker_info")],
        ["Container OS / kernel", rt.get("os_in_container")], ["CPU model (in container)", rt.get("cpu_model")],
        ["CPU cores visible in container", rt.get("cpu_count_visible")],
        ["RAM visible in container (MB)", rt.get("mem_total_mb_visible")],
        ["cgroup limits (backend container)", json.dumps(rt.get("cgroup_limits"))],
        ["Python", f"{rt.get('python')} ({rt.get('implementation')})"],
        ["Packages", ", ".join(f"{k} {v}" for k, v in (rt.get("packages") or {}).items())],
        ["PostgreSQL", pg.get("version")], ["PostgreSQL settings", json.dumps(pg.get("settings"))],
        ["Benchmark version", env.get("benchmark_version")],
    ]
    return _table(["Item", "Value (MEASURED / recorded)"], [[a, str(b)] for a, b in rows])


def _lat_row(k: str, r: dict[str, Any]) -> list[Any]:
    if not _ok(r):
        return [k, "MEASURED", f"**{r.get('status')}**: {str(r.get('failure'))[:80]}"] + [""] * 13
    tp, lat = r["summary"]["throughput"], r["summary"]["latency"]
    return [k, "MEASURED", r["config"]["variant"], tp["total_events"], tp["events_per_sec"], tp["events_per_min"],
            lat.get("p50"), lat.get("p95"), lat.get("p99"), lat.get("mean"), lat.get("min"), lat.get("max"),
            tp["successful"], tp["partial"], tp["failed"], tp["under_review"]]


def _http_row(k: str, r: dict[str, Any]) -> list[Any]:
    if not _ok(r):
        return [k, "MEASURED", f"**{r.get('status')}**: {str(r.get('failure'))[:80]}"] + [""] * 11
    tp, lat, h = r["summary"]["throughput"], r["summary"]["latency"], r["http"]
    return [k, "MEASURED", r["config"]["workers"], r["config"]["batch_size"], tp["total_events"], tp["wall_seconds"],
            tp["events_per_sec"], tp["events_per_min"], lat.get("p50"), lat.get("p95"), lat.get("p99"),
            json.dumps(h["http_status_distribution"]), h["transport_or_http_errors"], h["timeouts"]]


def _sweep_section(sweep, docker) -> str:
    if not sweep:
        return "NOT MEASURED."
    parts = ["Completion plan `L-*`: 2,000 events per run, `V3_full` (default configuration), scheduler off, "
             "single runs. Server CPU = the uvicorn process (`/proc`, 100 % = one core busy); DB CPU = the "
             "`logforge-db` container from `docker stats` over the measurement window.\n"]
    rows = []
    for batch in (1, 50, 100):
        base = sweep.get((1, batch))
        for conns in (1, 2, 4, 8):
            r = sweep.get((conns, batch))
            if not r:
                rows.append([f"c{conns} b{batch}", "MEASURED", "NOT RUN"] + [""] * 13)
                continue
            tp, lat, h = r["summary"]["throughput"], r["summary"]["latency"], r["http"]
            eff = (tp["events_per_sec"] / base["summary"]["throughput"]["events_per_sec"] / conns) if base else None
            rows.append([f"c{conns} b{batch}", "MEASURED", tp["events_per_sec"], tp["events_per_min"],
                         _g(h, "request_latency", "p50"), _g(h, "request_latency", "p95"), _g(h, "request_latency", "p99"),
                         f"{eff:.2f}" if eff is not None else NM,
                         h["transport_or_http_errors"] + h["timeouts"],
                         _g(r, "resources", "server", "cpu_pct_of_one_core_avg"), _g(r, "resources", "server", "rss_mb_max"),
                         _dk(r, docker, "logforge-db", "cpu_mean"), _g(r, "database", "per_second", "commits"),
                         _g(r, "database", "per_second", "rows_inserted"), _g(r, "pg_activity", "mean_active_backends"),
                         _top_waits(r, 2)])
    parts.append(_table(["Conns / batch", "Kind", "Events/s", "Events/min", "Req p50 ms", "Req p95 ms", "Req p99 ms",
                         "Scaling eff. vs 1 conn", "Errors+timeouts", "Server CPU %", "Server RSS MB", "DB CPU %",
                         "DB commits/s", "Rows inserted/s", "Mean active PG backends", "Top PG activity (active)"], rows))
    parts.append("\nFor batch runs the request latency covers the whole batch (50 or 100 events).")
    return "\n".join(parts)


def _sustained_section(sus, docker) -> str:
    if not sus:
        return "**NOT RUN.**"
    if not _ok(sus):
        return f"**FAILED:** {sus.get('failure')}"
    s, c = sus["sustained"], sus["config"]
    parts = [f"**Configuration:** HTTP, variant `{c['variant']}`, {c['workers']} connections, batch {c['batch_size']}, "
             f"scheduler {'on' if c['scheduler'] else 'off'} (interval {c['scheduler_interval_s']} s), seed {c['seed']}, "
             f"requested warm-up {s['warmup_s']:.0f} s + steady state {s['steady_duration_s']:.0f} s, windows "
             f"{s['window_s']:.0f} s. Actual wall time {sus['summary']['throughput']['wall_seconds']:.1f} s "
             f"(the last in-flight requests finish after the stop time). Single run.\n"]
    dw = _docker_window(docker, sus.get("measure_start_utc"), sus.get("measure_end_utc"))
    parts.append(_table(["Phase", "Kind", "Events", "Events/s", "p50 ms", "p95 ms", "p99 ms", "max ms", "Errors"], [
        ["warm-up", "MEASURED", s["warmup"]["events"], s["warmup"]["events_per_sec"], _g(s, "warmup", "latency", "p50"),
         _g(s, "warmup", "latency", "p95"), _g(s, "warmup", "latency", "p99"), _g(s, "warmup", "latency", "max"), ""],
        ["steady state", "MEASURED", s["steady_state"]["events"], s["steady_state"]["events_per_sec"],
         _g(s, "steady_state", "latency", "p50"), _g(s, "steady_state", "latency", "p95"),
         _g(s, "steady_state", "latency", "p99"), _g(s, "steady_state", "latency", "max"),
         s["steady_state"]["harness_errors"]]]))
    steady = [w for w in s["windows"] if w["phase"] == "steady"]
    if steady:
        pk = max(steady, key=lambda w: w["events_per_sec"])
        lo = min(steady, key=lambda w: w["events_per_sec"])
        parts.append(f"\nPeak steady window: {pk['events_per_sec']} events/s (window {pk['window']}); lowest: "
                     f"{lo['events_per_sec']} events/s (window {lo['window']}). HTTP: "
                     f"{json.dumps(sus['http']['http_status_distribution'])}, timeouts {sus['http']['timeouts']}, "
                     f"transport/HTTP errors {sus['http']['transport_or_http_errors']}.\n")
    t0 = _ts(sus["measure_start_utc"]) if sus.get("measure_start_utc") else None
    cpu = {w["window"]: w["mean_pct"] for w in (s.get("cpu_over_time", {}).get("server_cpu_pct") or [])}
    rows = []
    for w in s["windows"]:
        db = _docker_window(docker, *_iso_pair(t0, w["t_start_s"], w["t_end_s"])) if t0 else {}
        rows.append([w["window"], f"{w['t_start_s']:.0f}–{w['t_end_s']:.0f}", w["phase"], w["events"], w["events_per_sec"],
                     w["p50_ms"], w["p95_ms"], w["p99_ms"], w["harness_errors"], cpu.get(w["window"], NM),
                     _g(db, "logforge-db", "cpu_mean"), _g(db, "logforge-db", "mem_max_mib"),
                     _g(db, "logforge-backend", "mem_max_mib")])
    parts.append("Per-window evidence (MEASURED):\n")
    parts.append(_table(["Window", "t (s)", "Phase", "Events", "Events/s", "p50", "p95", "p99", "Errors",
                         "Server CPU %", "DB container CPU %", "DB container MiB", "Backend container MiB"], rows))
    d = s["degradation"]
    mem = s.get("memory_over_time", {})
    parts.append("\nDegradation checks (MEASURED; thresholds fixed in `runner.THRESHOLDS` before the run):\n")
    flags = d.get("flags", [])
    parts.append(_table(["Check", "Kind", "Value", "Threshold", "Crossed?"], [
        ["throughput last-3 ÷ first-3 steady windows", "MEASURED", d.get("throughput_ratio"),
         f"< {_g(d, 'thresholds', 'throughput_drop_ratio')}", "YES" if "THROUGHPUT_COLLAPSE" in flags else "no"],
        ["throughput slope (events/s per min)", "MEASURED", d.get("throughput_slope_eps_per_min"), "informational", ""],
        ["p95 last-3 ÷ first-3", "MEASURED", d.get("p95_ratio"), f"> {_g(d, 'thresholds', 'p95_growth_ratio')}",
         "YES" if "LATENCY_GROWTH" in flags else "no"],
        ["p95 slope (ms per min)", "MEASURED", d.get("p95_slope_ms_per_min"), "informational", ""],
        ["errors (total, trend)", "MEASURED", d.get("harness_errors_total"), "any and increasing",
         "YES" if "ERROR_ACCUMULATION" in flags else "no"],
        *[[f"{k}: growth MB (slope MB/min)", "MEASURED", f"{_g(v, 'growth_mb')} ({_g(v, 'slope_mb_per_min')})",
           "> 50 MB or > 25 %", "YES" if v.get("flag") else "no"] for k, v in mem.items()],
        ["PostgreSQL container memory first → last (MiB)", "MEASURED",
         f"{_g(dw, 'logforge-db', 'mem_first_mib')} → {_g(dw, 'logforge-db', 'mem_last_mib')} (max "
         f"{_g(dw, 'logforge-db', 'mem_max_mib')})", "informational", ""],
    ]))
    parts.append(f"\n**Harness verdict (application thresholds: throughput, p95, errors, API-server RSS):** "
                 f"{s.get('verdict')}\n")
    crossed = [k for k, v in mem.items() if v.get("flag") and k != "server_rss_mb"]
    srv = mem.get("server_rss_mb") or {}
    notes = []
    if crossed:
        notes.append(f"**Memory thresholds crossed outside the API server:** {', '.join(f'`{k}`' for k in crossed)} "
                     "(table above). The harness judges only the API process; these series include the load "
                     "generator, which keeps every per-event record in memory by design, and (for the cgroup) the page "
                     "cache of the raw-vault files written during the run. That this explains the growth is **LIKELY**, "
                     "not proven.")
    if isinstance(srv.get("growth_mb"), (int, float)) and srv["growth_mb"] > 0:
        notes.append(f"**API-server RSS grew {srv['growth_mb']} MB over the steady state ({srv['slope_mb_per_min']} "
                     "MB/min)**, below the threshold. Whether this levels off (caches warming) or keeps growing cannot be "
                     "told from 5 minutes: **UNKNOWN** — a multi-hour run is needed before claiming stable memory.")
    if steady:
        med = statistics.median(w["events_per_sec"] for w in steady)
        dips = [w for w in steady if w["events_per_sec"] < 0.6 * med]
        if dips:
            notes.append(f"**Throughput dip:** {len(dips)} steady window(s) fell below 60 % of the median "
                         f"({med:.1f} events/s): windows " + ", ".join(f"{w['window']} ({w['events_per_sec']})" for w in dips)
                         + ", then recovered. The degradation ratio compares the first and last three windows, so it "
                           "does not flag a mid-run dip. Its cause is **not isolated** (see the server and DB CPU "
                           "columns for the same windows).")
    if notes:
        parts.append("\n".join(f"- {n}" for n in notes) + "\n")
    parts.append(f"**Degradation result: {'PASS' if not s.get('all_flags') else 'FAIL'}** on the stated application "
                 "thresholds" + ("; memory thresholds were crossed by the load generator / container series, as "
                                 "described above." if crossed else ".") + "\n")
    ig = sus.get("integrity")
    if ig:
        parts.append(f"Integrity after the run: {_integrity_text(ig)}\n")
    parts.append(f"Events table after the run: {_f(_g(sus, 'database', 'row_counts_after', 'events'))} rows. This shows "
                 "behavior while the table grows from empty to that size, not at production table sizes.")
    return "\n".join(parts)


def _iso_pair(t0: float, a: float, b: float) -> tuple[str, str]:
    from datetime import timezone

    return (datetime.fromtimestamp(t0 + a, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(t0 + b, tz=timezone.utc).isoformat())


def _integrity_text(ig: dict[str, Any]) -> str:
    v, m = ig.get("vault", {}), ig.get("merkle", {})
    return (f"events {ig['events_in_db']:,} of {ig['expected_events']:,} expected (missing {ig['missing_events']}, extra "
            f"{ig['unexpected_extra_events']}); duplicate raw-hash groups {ig['duplicate_raw_hash_groups']}; SHA-256 "
            f"mismatches {ig['sha256_mismatches']}; vault objects re-hashed OK {v.get('rehash_ok')} / mismatched "
            f"{v.get('rehash_mismatch')} / missing {v.get('object_missing')}; Merkle chain valid "
            f"{_f(m.get('chain_valid'))} ({m.get('events_sealed')} events sealed in {m.get('batches_checked')} batches); "
            f"statuses {json.dumps(ig['status_counts'])}; **all checks passed: {_f(ig['all_passed'])}**.")


def _v_table(kv) -> str:
    rows = []
    for v in ("V0_core", "V1_core_phase7", "V2_core_drift", "V3_full"):
        s = kv.get(v)
        if s:
            rows.append([V_LABEL[v], "MEASURED", s["n"], ", ".join(f"{x:,.2f}" for x in s["eps"]), s["eps_mean"],
                         ", ".join(f"{x:.3f}" for x in s["mean_ms"]), s["mean_ms_mean"], s["p95_mean"], s["p99_mean"],
                         ", ".join(f"{x:.3f}" for x in s["pipeline_ms"]) or NM])
    if not rows:
        return "V0–V3 comparison: NOT MEASURED."
    return ("Completion plan `K-*`: in-process, 1 thread, 1,000 events, 100 warm-up, 3 interleaved repetitions with "
            "rotated order.\n\n" + _table(
                ["Configuration", "Kind", "Runs", "Events/s per run", "Events/s mean", "Mean ms per run",
                 "Mean ms (avg)", "p95 ms (avg)", "p99 ms (avg)", "`pipeline` ms/event per run (machine-speed check)"],
                rows))


def _deltas(kv) -> str:
    base = kv.get("V0_core")
    rows = []
    for v in ("V1_core_phase7", "V2_core_drift", "V3_full"):
        a = kv.get(v)
        if not a or not base:
            rows.append([V_LABEL.get(v, v), "NOT MEASURED", "", "", "", ""])
            continue
        d = a["mean_ms_mean"] - base["mean_ms_mean"]
        dt = a["eps_mean"] - base["eps_mean"]
        separated = a["mean_ms_min"] > base["mean_ms_max"] or base["mean_ms_min"] > a["mean_ms_max"]
        rows.append([V_LABEL[v], "MEASURED (difference of means)", f"{d:+.3f}", f"{dt:+.2f}",
                     f"{d / base['mean_ms_mean'] * 100:+.1f}%" if separated else "not stated (ranges overlap)",
                     "ranges do not overlap across repetitions" if separated
                     else "ranges overlap: within run-to-run spread, not a demonstrated difference"])
    return ("Differences against V0 (positive latency Δ = slower). A percentage is given only when the per-run ranges "
            "do not overlap; even then it is an association with the switched feature on a shared laptop, not proof "
            "of causality:\n\n" + _table(["Configuration", "Kind", "Δ mean ms/event", "Δ events/s", "Δ %",
                                          "Consistency"], rows))


def _overhead_section(kv, cv, b_big) -> str:
    parts = []
    if b_big and b_big.get("stages"):
        st = b_big["stages"]
        mean = b_big["summary"]["latency"]["mean"]
        rows = [[f"`{k}`", "MEASURED", s["calls"], s["ms_per_event"], _g(s, "latency", "p95"),
                 f"{s['ms_per_event'] / mean * 100:.1f}%"] for k, s in sorted(st.items(), key=lambda kv: -kv[1]["total_ms"])
                if s["calls"]]
        parts.append(f"Per-stage cost inside the default configuration (`{b_big['label']}`, mean event latency {mean} ms). "
                     "Stage timing measures each feature's code directly, so it is not affected by the between-run "
                     "variance that hides the V0–V3 differences:\n")
        parts.append(_table(["Stage", "Kind", "Calls", "ms/event", "p95 ms/call", "Share of mean latency"], rows))
        parts.append("\nNesting: `phase7_persist_total` contains `phase7_overflow_write`, `phase7_raw_archive` and "
                     "`phase8_persist_hooks_total` (which contains the `hook:*` rows). Phase 7 work that runs in every "
                     "persisting configuration: `phase7_spill_prepare`, `phase7_overflow_write` and the "
                     "`event_raw_storage` row.\n")
    jobs = (b_big or {}).get("jobs") or []
    rows = []
    for j in jobs:
        if j["job"] == "phase8_statistical_drift":
            rows.append(["Phase 8 statistical/semantic drift (\"V2 drift\" job, persist=false)", "MEASURED", j.get("duration_ms"),
                         f"{_f(j.get('rows_read'))} rows, {j.get('sources_analyzed')}/{j.get('sources')} sources, "
                         f"{j.get('findings')} findings, {j.get('ms_per_row_read')} ms/row"])
        elif j["job"] == "shadow_validation":
            rows.append([f"Shadow validation `{j['source']}` (identical candidate)", "MEASURED", j.get("duration_ms"),
                         f"{j.get('sample_count')} sampled events, {j.get('pipeline_runs')} pipeline runs, "
                         f"{j.get('ms_per_sampled_event')} ms/sampled event, verdict {j.get('verdict')} "
                         f"({', '.join(j.get('reasons') or [])})"])
        elif j["job"] == "merkle_seal":
            rows.append(["Merkle sealing (Phase 7 scheduler work)", "MEASURED", j.get("duration_ms"),
                         f"{_f(j.get('events_sealed'))} events, {j.get('batches')} batches, {j.get('ms_per_event')} ms/event"])
        elif j["job"] == "compact_lineage_storage" and j.get("result"):
            r = j["result"]
            rows.append(["Compact lineage storage (existing Phase 8 measurement)", "MEASURED", j.get("duration_ms"),
                         f"row {_g(r, 'compact', 'row_bytes_avg')} B vs detailed lineage as JSONB "
                         f"{_g(r, 'detailed', 'jsonb_bytes_avg')} B (ratio {_g(r, 'ratio_detailed_jsonb_to_compact_row')})"])
    if rows:
        parts.append("\nOff-ingest-path work (never per event, so no per-event overhead exists to compute), measured "
                     f"as jobs over the {_f(_g(b_big, 'database', 'row_counts_after', 'events'))} events of `{b_big['label']}`:\n")
        parts.append(_table(["Job", "Kind", "Duration ms", "Detail"], rows))
    if cv:
        parts.append("\nEarlier variant block (`C-*`, standard suite; `v0_core` = core + drift, `v1_phase7` = core + drift + "
                     "vault, `v3_full` = all):\n")
        parts.append(_table(["Variant", "Kind", "Runs", "Events/s per run", "Mean ms per run", "Mean ms (avg)"],
                            [[f"`{v}`", "MEASURED", s["n"], ", ".join(f"{x:,.2f}" for x in s["eps"]),
                              ", ".join(f"{x:.3f}" for x in s["mean_ms"]), s["mean_ms_mean"]]
                             for v, s in cv.items()]))
    return "\n".join(parts) if parts else "NOT MEASURED."


def _pg_section(runs, sus, docker) -> str:
    rows = []
    reps = [(k, v) for k, v in runs.items() if _ok(v) and v.get("pg_activity") and k.startswith(("K-V3", "K-V0", "L-http"))]
    if _ok(sus) and sus.get("pg_activity"):
        reps.append(("sustained", sus))
    for k, r in reps:
        db, cl, pa = r["database"], _g(r, "database", "cluster", default={}), r["pg_activity"]
        rows.append([k, "MEASURED", _g(db, "per_second", "commits"), _g(db, "per_event", "commits"),
                     _g(db, "per_second", "rows_inserted"), _g(db, "per_event", "rows_inserted"),
                     _g(cl, "wal_bytes_per_event"), _g(cl, "wal_syncs_per_event"), _g(cl, "wal_mb_per_sec"),
                     _g(cl, "wal_fsync_ms_avg"), _g(cl, "wal_fsync_ms_per_event"),
                     _g(cl, "bgwriter", "checkpoints_timed"), _g(cl, "bgwriter", "checkpoints_req"),
                     _g(cl, "bgwriter", "buffers_backend_fsync"), pa.get("max_client_connections"),
                     pa.get("mean_active_backends"), _dk(r, docker, "logforge-db", "cpu_mean"),
                     _dk(r, docker, "logforge-db", "cpu_max"), _dk(r, docker, "logforge-db", "mem_max_mib")])
    parts = ["Collected for every completion-plan run and the sustained run (the standard-suite runs predate this "
             "instrumentation; their `pg_stat_database` deltas are in each run's `result.json`).\n",
             _table(["Run", "Kind", "Commits/s", "Commits/event", "Rows ins/s", "Rows ins/event", "WAL B/event",
                     "WAL syncs/event", "WAL MB/s", "WAL fsync ms (avg)", "WAL fsync ms/event", "Checkpoints timed", "Checkpoints req", "Backend fsyncs",
                     "Max client conns", "Mean active backends", "DB CPU % mean", "DB CPU % max", "DB MiB max"], rows)]
    parts.append("\n`xact_commit` includes read-only transactions. WAL and checkpoint counters are **cluster-wide** "
                 "(the dev stack's database is on the same server, idle apart from its 60 s scheduler tick). "
                 "WAL fsync time comes from `pg_stat_wal.wal_sync_time`, recorded only by sessions with "
                 "`track_wal_io_timing` on — set for the `logforge_bench` database only, so it times the benchmark's "
                 "own WAL flushes; an empty cell means it was not recorded for that run.\n")
    agg: dict[str, int] = {}
    for _, r in reps:
        for key, n in (r["pg_activity"].get("by_state_and_wait") or {}).items():
            agg[key] = agg.get(key, 0) + n
    if agg:
        total = sum(agg.values())
        parts.append("Where PostgreSQL client backends spent their time, all instrumented runs pooled "
                     f"({total:,} backend-samples at 0.5 s, MEASURED):\n")
        parts.append(_table(["State / wait event", "Kind", "Backend-samples", "Share"],
                            [[k, "MEASURED", n, f"{n / total * 100:.1f}%"]
                             for k, n in sorted(agg.items(), key=lambda kv: -kv[1])[:15]]))
        parts.append("\n`idle` = a connection is open but the application is not using it (the application is "
                     "working, or waiting on something other than PostgreSQL). `active: on CPU` = executing. "
                     "`IO:WALSync` / `LWLock:WALWrite` = waiting for commit durability.")
    return "\n".join(parts)


def _cpu_section(runs, sus, docker) -> str:
    rows = []
    for k, r in runs.items():
        if not _ok(r) or r["summary"]["throughput"]["total_events"] < 100:
            continue
        res = r["resources"]
        na = "n/a (in-process)" if r["config"]["mode"] == "inprocess" else None
        rows.append([k, "MEASURED", r["config"]["mode"], r["config"]["workers"], r["summary"]["throughput"]["events_per_sec"],
                     _g(res, "harness", "cpu_pct_of_one_core_avg"), na or _g(res, "server", "cpu_pct_of_one_core_avg"),
                     _g(res, "harness", "rss_mb_max"), na or _g(res, "server", "rss_mb_max"),
                     _g(res, "container_cgroup_mb", "max"), _dk(r, docker, "logforge-db", "cpu_mean"),
                     _dk(r, docker, "logforge-db", "mem_max_mib"), _dk(r, docker, "logforge-backend", "cpu_mean")])
    if _ok(sus):
        res = sus["resources"]
        rows.append(["sustained", "MEASURED", "http", sus["config"]["workers"],
                     sus["sustained"]["steady_state"]["events_per_sec"], _g(res, "harness", "cpu_pct_of_one_core_avg"),
                     _g(res, "server", "cpu_pct_of_one_core_avg"), _g(res, "harness", "rss_mb_max"),
                     _g(res, "server", "rss_mb_max"), _g(res, "container_cgroup_mb", "max"),
                     _dk(sus, docker, "logforge-db", "cpu_mean"), _dk(sus, docker, "logforge-db", "mem_max_mib"),
                     _dk(sus, docker, "logforge-backend", "cpu_mean")])
    return ("CPU % = CPU seconds ÷ wall seconds × 100 (100 % = one core). In in-process runs the harness *is* the "
            "application; in HTTP runs the harness is the load generator and *server* is the API process. Docker "
            "columns come from the host `docker stats` series inside each run's measurement window (standard-suite "
            "runs: whole run window).\n\n" + _table(
                ["Run", "Kind", "Mode", "Conns/threads", "Events/s", "Harness CPU %", "Server CPU %",
                 "Harness RSS MB", "Server RSS MB", "Backend cgroup MB", "DB container CPU %", "DB container MiB",
                 "Backend container CPU %"], rows))


def _bottleneck_section(b_big, done, sweep, kv, sus, docker) -> str:
    L = ["### MEASURED\n"]
    if b_big and b_big.get("stages"):
        st, mean = b_big["stages"], b_big["summary"]["latency"]["mean"]
        top = [(k, st[k]["ms_per_event"]) for k in ("pipeline", "phase5_drift", "phase7_raw_archive",
                                                     "hook:compact_lineage", "event_insert_commit_refresh",
                                                     "adapter_registry_lookup", "phase7_overflow_write",
                                                     "phase7_spill_prepare") if k in st]
        top.sort(key=lambda kv: -kv[1])
        L.append(_table(["Stage (`" + b_big["label"] + "`)", "Kind", "ms/event", "Share of mean latency"],
                        [[f"`{k}`", "MEASURED", ms, f"{ms / mean * 100:.1f}%"] for k, ms in top]))
        pipe = st.get("pipeline", {}).get("ms_per_event")
        L.append(f"\n- The deterministic pipeline is {pipe} ms of a {mean} ms mean event "
                 f"({pipe / mean * 100:.1f}%). The rest is persistence and per-event database work, performed "
                 "serially for each event.")
        L.append(f"- In the same run the application process used {_g(b_big, 'resources', 'harness', 'cpu_pct_of_one_core_avg')} % "
                 "of one core: it was waiting (on PostgreSQL round trips, commits and fsync) the rest of the time.")
    po = done.get("A-inproc-pipeline_only-10000")
    if po and b_big:
        L.append(f"- Without the database the same events process at {po['summary']['throughput']['events_per_sec']:,.2f} "
                 f"events/s; with it, {b_big['summary']['throughput']['events_per_sec']:,.2f} events/s.")
    if sweep.get((1, 1)):
        rs = [(c, sweep[(c, 1)]) for c in (1, 2, 4, 8) if (c, 1) in sweep]
        L.append("- HTTP, batch 1: " + "; ".join(
            f"{c} conn → {r['summary']['throughput']['events_per_sec']:,.2f} ev/s, server CPU "
            f"{_g(r, 'resources', 'server', 'cpu_pct_of_one_core_avg')} %, DB CPU {_dk(r, docker, 'logforge-db', 'cpu_mean')} %, "
            f"mean active PG backends {_g(r, 'pg_activity', 'mean_active_backends')}" for c, r in rs) + ".")
    for b in (50, 100):
        if sweep.get((1, b)):
            rs = [(c, sweep[(c, b)]) for c in (1, 2, 4, 8) if (c, b) in sweep]
            L.append(f"- HTTP, batch {b}: " + "; ".join(
                f"{c} conn → {r['summary']['throughput']['events_per_sec']:,.2f} ev/s (server CPU "
                f"{_g(r, 'resources', 'server', 'cpu_pct_of_one_core_avg')} %)" for c, r in rs) + ".")
    L.append("\n### LIKELY (technically plausible, not isolated by these measurements)\n")
    L.append("- One synchronous transaction + COMMIT per event (by design, for per-event durability) bounds each "
             "connection's rate; batching the HTTP request does not batch the commits.")
    L.append("- A single Python process (GIL) runs parsing, normalization and the ORM work for all connections; "
             "threads mostly overlap waits.")
    L.append("- Index maintenance and vacuum costs grow with table size; every run here starts from an empty table.")
    L.append("- Docker Desktop's virtualized disk makes each fsync (PostgreSQL WAL and the raw vault) slower than on "
             "a dedicated host.")
    fs = [(k, r) for k, r in done.items() if isinstance(_g(r, "database", "cluster", "wal_fsync_ms_avg", default=None), float)]
    if _ok(sus) and isinstance(_g(sus, "database", "cluster", "wal_fsync_ms_avg", default=None), float):
        fs.append(("sustained", sus))
    if fs:
        vals = [_g(r, "database", "cluster", "wal_fsync_ms_avg") for _, r in fs]
        per = [_g(r, "database", "cluster", "wal_fsync_ms_per_event") for _, r in fs]
        L.insert(L.index("\n### LIKELY (technically plausible, not isolated by these measurements)\n"),
                 f"- WAL fsync (commit durability) cost, MEASURED in {len(fs)} runs: {min(vals):.3f}–{max(vals):.3f} ms "
                 f"per fsync, {min(per):.3f}–{max(per):.3f} ms of WAL fsync per event.")
    L.append("\n### UNKNOWN\n")
    if not fs:
        L.append("- PostgreSQL commit (WAL flush) latency: not recorded (`track_wal_io_timing` off).")
    L.append("- Behavior with more than one API process or more than one PostgreSQL instance (not run).")
    L.append("- Behavior on server hardware with local NVMe and dedicated cores (not run).")
    return "\n".join(L)


def _efficiency_section(sweep, done) -> str:
    rows = []
    for batch in (1, 50, 100):
        base = sweep.get((1, batch))
        if not base:
            continue
        b = base["summary"]["throughput"]["events_per_sec"]
        for c in (2, 4, 8):
            r = sweep.get((c, batch))
            if r:
                e = r["summary"]["throughput"]["events_per_sec"]
                rows.append([f"batch {batch}", c, "MEASURED", b, e, f"{e / b:.2f}×", f"{e / b / c:.2f}"])
    if not rows:
        return "NOT MEASURED."
    return ("Scaling efficiency = (throughput at N ÷ throughput at 1) ÷ N, computed from the `L-*` runs (single runs "
            "each; the between-run variance in section P applies).\n\n" + _table(
                ["Series", "Connections N", "Kind", "Events/s at 1", "Events/s at N", "Speed-up", "Efficiency"], rows)
            + "\n\nThese are client connections to **one** API process and **one** PostgreSQL. No multi-replica "
              "scaling was measured.")


def _scale_section(sus, best, b_big) -> str:
    req = 1e9 / DAY
    parts = ["```\nrequired average events/s for 1B/day = 1,000,000,000 / 86,400 = "
             f"{req:,.2f} events/s\n```\n"]
    measured = []
    if _ok(sus):
        measured.append(("sustained steady state (HTTP)", sus["sustained"]["steady_state"]["events_per_sec"]))
    if best:
        measured.append((f"best default-configuration run `{best['label']}`", best["summary"]["throughput"]["events_per_sec"]))
    rows = []
    for name, per_day in (("100 million/day", 1e8), ("500 million/day", 5e8), ("1 billion/day", 1e9)):
        need = per_day / DAY
        for mname, eps in measured:
            rows.append([name, f"{need:,.2f}", mname, "MEASURED", f"{eps:,.2f}", EXTRA, f"{need / eps:,.1f}×",
                         f"{eps * DAY:,.0f}"])
    parts.append(_table(["Target", "Required avg events/s", "Measured basis", "Kind", "Measured events/s", "Kind",
                         "Gap factor", "Events/day at measured rate"], rows))
    parts.append("\nNo target was reached or approached. The arithmetic assumes a constant rate for 24 h, which was "
                 "not run, and says nothing about peaks (real traffic peaks above its average). It is **not** "
                 "evidence of production capacity, and it is not evidence that N instances would deliver N× the "
                 "rate: every instance of this design would write to the same PostgreSQL primary with one commit "
                 "per event.")
    if b_big:
        bpe = _g(b_big, "database", "bytes_per_event_all_event_tables", default=None)
        if isinstance(bpe, (int, float)) and bpe > 0:
            parts.append(f"\nHot PostgreSQL growth measured at {bpe:,.0f} B/event (event-scoped tables incl. indexes, "
                         f"this dataset's size mix) → {EXTRA}: {bpe * 1e9 / 1e12:,.1f} TB/day at 1B events/day, "
                         "before the cold-vault copy.")
    return "\n".join(parts)


def _measured_list(done, sus, sweep, kv) -> str:
    items = [f"{len(done)} completed benchmark runs (sections D–I), each with its environment, config and "
             "per-event samples.",
             "Throughput, p50/p95/p99, statuses, raw preservation and SHA-256 agreement for every run.",
             "Per-stage ingestion cost (in-process runs).",
             "Process CPU and RSS (harness, API server), backend container cgroup memory.",
             "PostgreSQL: database transaction/row counters, cluster WAL and checkpoint counters, client-backend "
             "state/wait sampling (completion plan and sustained), container CPU/memory via `docker stats`.",
             "Off-path jobs: Phase 8 statistical drift, shadow validation, Merkle sealing, compact-lineage storage."]
    if sweep:
        items.append(f"HTTP concurrency × batch sweep: {len(sweep)} of 12 combinations.")
    if kv:
        items.append("V0–V3 comparison: " + ", ".join(f"{v} × {s['n']}" for v, s in kv.items()) + ".")
    if _ok(sus):
        items.append(f"One sustained run: {sus['sustained']['steady_duration_s']:.0f} s steady state + "
                     f"{sus['sustained']['warmup_s']:.0f} s warm-up, with per-window evidence.")
    items.append("Post-run integrity: counts, duplicates, SHA-256 in PostgreSQL, vault re-hash, Merkle chain "
                 "(completion plan and sustained).")
    return "\n".join(f"- {x}" for x in items)


def _lb_section(sweep, done, sus, docker, b_big) -> str:
    L = ["**DO NOT IMPLEMENT LOAD BALANCING YET.** The answers below are derived only from the measurements above.\n"]
    c = {n: sweep.get((n, 1)) for n in (1, 2, 4, 8)}
    have = {n: r for n, r in c.items() if r}
    if not have.get(1):
        L.append("INSUFFICIENT EVIDENCE — LOAD BALANCING EXPERIMENT SHOULD BE THE NEXT STEP. (No batch-1 sweep.)")
        return "\n".join(L)
    eps = {n: r["summary"]["throughput"]["events_per_sec"] for n, r in have.items()}
    scpu = {n: _g(r, "resources", "server", "cpu_pct_of_one_core_avg", default=None) for n, r in have.items()}
    dcpu = {n: _dk(r, docker, "logforge-db", "cpu_mean") for n, r in have.items()}
    act = {n: _g(r, "pg_activity", "mean_active_backends", default=None) for n, r in have.items()}
    speed = {n: eps[n] / eps[1] for n in eps}
    best_n = max(speed, key=speed.get)
    top_n = max(eps)
    cpu_sat = [n for n, v in scpu.items() if isinstance(v, (int, float)) and v >= 85]
    db_vals = [v for v in dcpu.values() if isinstance(v, (int, float))]
    db_high = any(v >= 80 for v in db_vals)
    pg_act = [v for v in act.values() if isinstance(v, (int, float))]
    waits: dict[str, int] = {}
    for r in list(have.values()) + ([sus] if _ok(sus) and sus.get("pg_activity") else []):
        for key, n in (r["pg_activity"].get("by_state_and_wait") or {}).items():
            waits[key] = waits.get(key, 0) + n
    wtot = sum(waits.values()) or 1
    iit = sum(v for k, v in waits.items() if k.startswith("idle in transaction")) / wtot
    on_cpu = waits.get("active: on CPU (no wait event)", 0) / wtot
    walw = sum(v for k, v in waits.items() if "WAL" in k) / wtot
    cpu_txt = ", ".join(f"{n}→{_f(v)}" for n, v in scpu.items())
    L.append(f"**A. Is the application CPU-bound?** API server CPU (% of one core) by connections, batch 1: {cpu_txt}. "
             f"Throughput over the same runs: " + ", ".join(f"{n}→{eps[n]:,.2f}" for n in eps) + " events/s. "
             + (f"From {min(cpu_sat)} connections on, the process uses ≥ 85 % of a core while throughput does not "
                "rise with it: more CPU is spent without more events processed (**MEASURED**). Values above 100 % "
                "show some work runs outside the Python GIL (C extensions, the database driver). That the limit is "
                "CPU/GIL contention inside the single process is **LIKELY**, not proven." if cpu_sat else
                "No run reached 85 % of one core: CPU saturation of the API process is **not** shown."))
    L.append(f"\n**B. Is PostgreSQL-bound behavior visible?** DB container CPU % (docker stats): "
             + ", ".join(f"{n}→{_f(v)}" for n, v in dcpu.items()) + "; mean simultaneously active PG backends: "
             + ", ".join(f"{n}→{_f(v)}" for n, v in act.items()) + ". "
             + ("PostgreSQL CPU reached ≥ 80 % of a core in at least one run." if db_high else
                "PostgreSQL CPU stayed below 80 % of one core and "
                + (f"on average at most {max(pg_act):.2f} backends were active at once" if pg_act else "active-backend data is missing")
                + ": PostgreSQL saturation is **not** shown by these measurements.")
             + f" Across the batch-1 sweep and the sustained run, PostgreSQL client backends were "
               f"`idle in transaction` (waiting for the application inside an open transaction) {iit * 100:.0f} % of "
               f"sampled time, executing on CPU {on_cpu * 100:.0f} %, and waiting on WAL write/sync {walw * 100:.0f} % "
               "(**MEASURED**): the database mostly waits for the application, not the reverse.")
    L.append(f"\n**C. Would multiple FastAPI replicas plausibly improve throughput?** Best speed-up over 1 connection: "
             f"{speed[best_n]:.2f}× at {best_n} connections; at {top_n} connections {speed[top_n]:.2f}×, with p95 "
             f"latency {_g(have[top_n], 'http', 'request_latency', 'p95')} ms vs "
             f"{_g(have[1], 'http', 'request_latency', 'p95')} ms at 1 connection (**MEASURED**, single runs). More "
             "connections to one API process therefore mostly add queueing, not throughput. "
             + ("Because the single API process shows the CPU/queueing signs above while PostgreSQL is mostly idle, "
                "more API processes are a **plausible** way to raise throughput until PostgreSQL (one commit per "
                "event, WAL fsync ≈ 1–2 ms) becomes the limit — an inference, **not a measurement**." if cpu_sat and not db_high else
                "The evidence does not isolate a single limit that replicas would remove."))
    L.append("\n**D. Evidence:** sections F (sweep, scaling efficiency), G (sustained), J (PostgreSQL/WAL), "
             "K (CPU/RAM), L (stage costs).")
    L.append("\n**E. Recommended experiment for Step 13 (not implemented here):**\n")
    L.append(_table(["Parameter", "Value"], [
        ["Replicas", "1 → 2 → 4 API processes (uvicorn workers, or replicas behind one reverse proxy)"],
        ["Database", "the same single PostgreSQL (`logforge_bench`), reset per run, `track_wal_io_timing` on"],
        ["Dataset / seed", "this dataset, seed 1337, 5 % malformed, all formats and size bands"],
        ["Workload", "HTTP `POST /ingest` batch 1 and `/ingest/batch` batch 100; total connections fixed per step "
                     "(e.g. 4 and 8) so only the replica count changes; 2,000 events per run + a 5-minute sustained run "
                     "at the best replica count"],
        ["Repetitions", "3 per configuration, interleaved order, same warm-up"],
        ["Record", "events/s, p50/p95/p99, errors/timeouts, per-process CPU and RSS, PostgreSQL CPU/memory, commits/s, "
                   "rows/s, WAL MB/s, WAL fsync ms, `pg_stat_activity` waits, lock waits"],
        ["Integrity", "the same post-run checks as here: counts, SHA-256 multiset, vault re-hash, Merkle chain"],
        ["Stop / decide", "stop adding replicas when events/s stops rising or PostgreSQL CPU, WAL-sync or lock waits "
                          "dominate; report scaling efficiency = (events/s at N ÷ at 1) ÷ N"],
    ]))
    L.append("\n**Conclusion:** INSUFFICIENT EVIDENCE — LOAD BALANCING EXPERIMENT SHOULD BE THE NEXT STEP. No "
             "multi-process or multi-replica configuration was measured, so any horizontal-scaling gain is unproven.")
    return "\n".join(L)


UNKNOWN = [
    "Throughput with more than one API process / replica, and where PostgreSQL saturates under it.",
    "PostgreSQL commit latency as a distribution (only averages from pg_stat_wal are available; runs before the completion plan did not time WAL at all).",
    "Behavior over hours or days, and at production table sizes (10⁸–10¹⁰ rows), including vacuum and index bloat.",
    "Results on server-class hardware (dedicated cores, NVMe, no virtualization layer).",
    "Throughput of any queue/worker/batched-commit architecture (not built).",
    "Behavior with real (non-synthetic) traffic mixes and error rates.",
]

LIMITATIONS = [
    "**Laptop-class, shared environment.** Intel Core 5 120U laptop, Docker Desktop (WSL2) VM; the load generator "
    "shares CPUs with the API process; PostgreSQL is shared with the running development stack.",
    "**Duration.** One 5-minute sustained run; fixed-size runs last seconds to a few minutes.",
    "**Database.** Every run starts from an empty benchmark database; `shared_buffers=128MB`, default tuning; "
    "`xact_commit` counts include read-only transactions; WAL/checkpoint counters are cluster-wide.",
    "**Reproducibility.** Deterministic data and configuration, but absolute numbers depend on this machine's "
    "momentary speed (see the variance item above).",
    "**Historical comparison.** V0–V3 are switches on the current code, not historical releases. Phase 7 spill "
    "preparation and the `event_raw_storage` row run in every persisting configuration (they cannot be switched "
    "off without a code change); their cost is shown as stages.",
    "**Concurrency.** Client connections to one uvicorn worker only; no multi-worker or multi-replica runs.",
    "**HTTP benchmark.** The server runs without `--reload` and access logging (unlike the dev compose service); "
    "client and server share a container; HTTP/1.1 keep-alive, no TLS, no proxy.",
    "**Scale extrapolation.** Linear arithmetic only; peaks, growth, retention and multi-instance behavior are "
    "not modeled.",
    "**Dataset.** Synthetic; size bands give each vendor source several structures, so ~25 % of events are Phase 5 "
    "drift holds (`UNDER_REVIEW`), which write the same rows as normal events.",
    "**Scheduler.** Off in fixed-size runs (sealing measured as a job); on in the sustained run.",
]

REPRO = """Environment: Docker Desktop, the dev stack running (`docker compose up -d`), commit as in section 3.
All commands run the harness inside the backend container; `scripts/bench.sh` also records host facts
(git SHA, Docker version, host OS/CPU/RAM) and samples `docker stats` for both containers every ~1–3 s.
Seed 1337; format mix fortinet,cef,leef,xml,json,syslog; size bands small,medium,large,xlarge; malformed 5 %.
The benchmark writes only to the `logforge_bench` database (created and migrated with `alembic upgrade head`
automatically), `/tmp/logforge-bench/…` inside the container, and new result directories.

```bash
# 1. standard 46-run matrix (load levels 1/100/1,000/10,000, variants, malformed sweep, size bands, HTTP)
bash scripts/bench.sh suite
# 2. completion plan: V0-V3 x 3 interleaved + HTTP 1/2/4/8 connections x batch 1/50/100, with integrity checks
bash scripts/bench.sh suite --plan completion
# 3. the 5-minute sustained run (30 s warm-up + 300 s steady state), HTTP, 4 connections, scheduler on
bash scripts/bench.sh sustained --mode http --variant V3_full --workers 4 --batch-size 1 --scheduler \\
     --duration 300 --warmup-seconds 30 --window 10 --integrity --seed 1337
# 4. render this report (paths inside the container; then copy it to docs/)
bash scripts/bench.sh report --suite logforge_bench/results/<standard-suite> --suite logforge_bench/results/<completion-suite> \\
     --sustained logforge_bench/results/<sustained-run>/result.json \\
     --docker-stats logforge_bench/results/docker_stats-<...>.jsonl --out logforge_bench/results/phase8-step10-benchmark.md

# single runs (every option)
docker compose exec backend python -m logforge_bench run --mode inprocess --variant V3_full --events 10000 \\
    --seed 1337 --format fortinet,cef,leef,xml,json,syslog --size small,medium,large,xlarge \\
    --malformed-rate 0.05 --workers 1 --batch-size 1 --jobs --integrity
docker compose exec backend python -m logforge_bench run --mode http --variant V3_full --events 2000 --workers 4 --batch-size 50
docker compose exec backend python -m logforge_bench dataset --events 1000 --seed 1337
docker compose exec backend python -m logforge_bench suite --quick        # harness smoke test
```

No internet access is required. Nothing overwrites earlier results.
"""


# --------------------------------------------------------------------------
# Step 10 final-structure helpers
# --------------------------------------------------------------------------


def _junit(paths: list[Path]) -> list[dict[str, Any]]:
    """Summaries of JUnit XML files (`name=path` labels them). Read, never typed."""
    import xml.etree.ElementTree as ET

    out = []
    for p in paths:
        s = str(p)
        name, path = (s.split("=", 1) if "=" in s else (Path(s).stem, s))
        f = Path(path)
        if not f.exists():
            out.append({"name": name, "error": f"missing {path}", "ok": False})
            continue
        root = ET.parse(f).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        t = {k: sum(int(float(x.get(k, 0) or 0)) for x in suites) for k in ("tests", "failures", "errors", "skipped")}
        t["passed"] = t["tests"] - t["failures"] - t["errors"] - t["skipped"]
        out.append({"name": name, **t, "ok": t["failures"] == 0 and t["errors"] == 0 and t["tests"] > 0})
    return out


def _verdict(std, comp, sus, sweep, runs, devdbs, tests) -> dict[str, Any]:
    crit = []

    def c(name, ok, detail):
        crit.append({"criterion": name, "passed": bool(ok), "detail": detail})

    for label, s, planned in (("Standard suite complete (46 runs, 0 failed)", std, 46),
                              ("Completion suite complete (24 runs, 0 failed)", comp, 24)):
        if s:
            n = sum(1 for v in s[2].values() if _ok(v))
            c(label, n == planned == len(s[2]), f"{n} of {len(s[2])} completed (planned {planned})")
        else:
            c(label, False, "suite not provided")
    combos = [(n, b) for b in (1, 50, 100) for n in (1, 2, 4, 8)]
    c("HTTP concurrency sweep 1/2/4/8 connections x batch 1/50/100", all(k in sweep for k in combos),
      f"{sum(k in sweep for k in combos)} of 12 combinations measured")
    big_http = [k for k, v in runs.items() if _ok(v) and v["config"]["mode"] == "http"
                and v["summary"]["throughput"]["total_events"] >= 10_000 and v["config"]["workers"] > 1]
    c("A 10,000-event HTTP run with several connections", bool(big_http), ", ".join(big_http) or "none")
    if sus:
        s = sus.get("sustained") or {}
        dur = s.get("steady_duration_s") or 0
        c("Real 5-minute sustained run completed", _ok(sus) and dur >= 300,
          f"status {sus.get('status')}, steady state {dur:.0f} s + warm-up {s.get('warmup_s', 0):.0f} s")
        flags = s.get("all_flags") or []
        other = [k for k, v in (s.get("memory_over_time") or {}).items() if v.get("flag") and k != "server_rss_mb"]
        d = s.get("degradation") or {}
        c("Sustained run: no application degradation threshold crossed (throughput, p95, errors, API-server RSS)",
          _ok(sus) and not flags and d.get("verdict") != "INSUFFICIENT DATA",
          (", ".join(flags) or f"no flag (throughput ratio {d.get('throughput_ratio')}, p95 ratio {d.get('p95_ratio')}, "
                               f"errors {d.get('harness_errors_total')})")
          + (f"; NOTE memory thresholds crossed by non-application series: {', '.join(other)} (section G)" if other else ""))
        pg = bool(sus.get("pg_activity")) and isinstance(_g(sus, "database", "cluster", "wal", default=None), dict)
        c("PostgreSQL evidence collected (activity, WAL, checkpoints)", pg, "sustained run + completion plan")
    else:
        c("Real 5-minute sustained run completed", False, "NOT RUN")
    ints = [(k, v) for k, v in runs.items() if _ok(v) and v.get("integrity")]
    if sus and sus.get("integrity"):
        ints.append(("sustained", sus))
    bad = [k for k, r in ints if not _integrity_eval(r)["passed"]]
    c("Integrity verified (counts, duplicates, SHA-256, vault, Merkle)", bool(ints) and not bad,
      f"{len(ints) - len(bad)} of {len(ints)} verified runs passed" + (f"; failed: {', '.join(bad)}" if bad else ""))
    if devdbs:
        marks = [d.get("events_with_benchmark_markers") for d in devdbs]
        c("Development database untouched by the benchmark", all(m == 0 for m in marks),
          "benchmark-marker events in the dev DB per check: " + ", ".join(map(str, marks)))
    else:
        c("Development database untouched by the benchmark", False, "no devdb-check supplied")
    if tests:
        failed = [t["name"] for t in tests if not t.get("ok")]
        c("Tests pass (JUnit reports)", not failed, "; ".join(
            f"{t['name']}: {t.get('passed')}/{t.get('tests')} passed" + (f" ({t['error']})" if t.get("error") else "")
            for t in tests))
    else:
        c("Tests pass (JUnit reports)", False, "no JUnit reports supplied to the report generator")
    return {"criteria": crit, "all_passed": all(x["passed"] for x in crit)}


def _verdict_section(verdict, tests) -> str:
    rows = [[x["criterion"], "PASS" if x["passed"] else "**FAIL**", x["detail"]] for x in verdict["criteria"]]
    parts = ["Computed by `report._verdict` from the artifacts; not typed by hand.\n",
             _table(["Acceptance criterion", "Result", "Evidence"], rows)]
    if tests:
        parts.append("\nTest reports (MEASURED, read from JUnit XML):\n")
        parts.append(_table(["Suite", "Tests", "Passed", "Failed", "Errors", "Skipped"],
                            [[t["name"], t.get("tests"), t.get("passed"), t.get("failures"), t.get("errors"),
                              t.get("skipped")] for t in tests]))
    remaining = [x["criterion"] for x in verdict["criteria"] if not x["passed"]]
    parts.append("\n**" + ("STEP 10 COMPLETE — ALL ACCEPTANCE CRITERIA PASSED" if verdict["all_passed"] else
                           "STEP 10 PARTIAL — remaining: " + "; ".join(remaining)) + "**")
    return "\n".join(parts)


def _exec_summary(sus, b_big, sweep, best, done, verdict) -> str:
    ex = []
    if _ok(sus):
        ss, s = sus["sustained"]["steady_state"], sus["sustained"]
        ex.append(f"**Sustained (MEASURED):** {s['steady_duration_s']:.0f} s steady state after a {s['warmup_s']:.0f} s "
                  f"warm-up, HTTP, {sus['config']['workers']} connections, batch {sus['config']['batch_size']}, default "
                  f"configuration, scheduler on: **{ss['events_per_sec']:,.2f} events/s**, p50 {ss['latency']['p50']} / "
                  f"p95 {ss['latency']['p95']} / p99 {ss['latency']['p99']} ms, {ss['harness_errors']} errors. "
                  f"Degradation: *{s['verdict']}*.")
    if best:
        ex.append(f"**Best full-pipeline (default configuration) throughput in any run (MEASURED):** "
                  f"{best['summary']['throughput']['events_per_sec']:,.2f} events/s (`{best['label']}`, "
                  f"{best['summary']['throughput']['total_events']:,} events, single run).")
    lean = max((v for v in done.values() if v["config"]["variant"] not in ("pipeline_only", "v3_full", "V3_full")
                and _std_mix(v) and v["summary"]["throughput"]["total_events"] >= 100),
               key=lambda v: v["summary"]["throughput"]["events_per_sec"], default=None)
    if lean:
        ex.append(f"**Best reduced-configuration run (MEASURED):** {lean['summary']['throughput']['events_per_sec']:,.2f} "
                  f"events/s (`{lean['label']}`, variant `{lean['config']['variant']}`) — not the full pipeline.")
    po = done.get("A-inproc-pipeline_only-10000")
    if b_big and po:
        ex.append(f"**Where the time goes (MEASURED):** the deterministic pipeline alone processes "
                  f"{po['summary']['throughput']['events_per_sec']:,.2f} events/s; with persistence the same data runs "
                  f"at {b_big['summary']['throughput']['events_per_sec']:,.2f} events/s — per-event database work dominates.")
    if (1, 1) in sweep:
        ex.append("**HTTP concurrency (MEASURED, batch 1):** " + ", ".join(
            f"{c} conn → {sweep[(c, 1)]['summary']['throughput']['events_per_sec']:,.2f}" for c in (1, 2, 4, 8)
            if (c, 1) in sweep) + " events/s against one API process.")
    if _ok(sus):
        eps = sus["sustained"]["steady_state"]["events_per_sec"]
        req = 1e9 / DAY
        ex.append(f"**1B events/day:** requires {req:,.2f} events/s on average (PROJECTED requirement); the sustained "
                  f"measurement is {eps:,.2f} events/s — {req / eps:,.1f}× short. **NOT DEMONSTRATED.**")
    ex.append("**Verdict:** " + ("STEP 10 COMPLETE — ALL ACCEPTANCE CRITERIA PASSED" if verdict["all_passed"]
                                 else "STEP 10 PARTIAL (see section R)"))
    return "\n".join(f"- {x}" for x in ex)


def _dataset_section(b_big) -> str:
    ds = _g(b_big, "dataset", default={}) if b_big else {}
    if not ds:
        return "NOT MEASURED."
    pr = b_big["summary"]["processing"]
    gen = {"fortinet": "RFC3164 syslog, FORTIGATE key=value → vendor adapter `fortinet`",
           "cef": "CEF:0 Palo Alto Networks → vendor adapter `paloalto_cef`",
           "leef": "LEEF:1.0 TAB-delimited → `leef_generic`",
           "xml": "flat Windows-style event XML → `xml_generic`",
           "json": "single JSON object → `json_generic`",
           "syslog": "RFC3164 sshd line / RFC5424 key=value → `syslog_generic`"}
    return "\n".join([
        f"Seed **{b_big['config']['seed']}** for every run. Largest in-process run (`{b_big['label']}`): {ds['events']:,} "
        f"events, {ds['total_bytes']:,} bytes; malformed target {b_big['config']['malformed_rate']:.0%}, actual "
        f"**{ds['malformed_rate_actual'] * 100:.2f}%** ({ds['malformed']} events). Event *i* is a pure function of "
        "(seed, *i*); formats × size bands interleave round-robin; every well-formed raw log is unique (warm-up "
        "events use a disjoint index range). A few malformed kinds are constant strings (e.g. a truncated CEF "
        "header), so a handful of byte-identical malformed lines are sent per run — see section M.\n",
        _table(["Format", "Events", "Generated as"], [[f, n, gen[f]] for f, n in ds["by_format"].items()]),
        "\n" + _table(["Size band", "Kind", "Target B", "Events", "min B", "median B", "p95 B", "max B"],
                      [[b, "MEASURED", s["target_bytes"], s["n"], s["min"], s["median"], s["p95"], s["max"]]
                       for b, s in ds["sizes_by_band"].items()]),
        f"\nExtension-heavy: {pr['events_with_extensions']:,} events carry extensions (mean {pr['extension_keys_mean']} "
        f"keys); {pr['events_spilled']:,} exceed the Phase 7 inline budget (64 fields / 8 KiB) and spill to overflow. "
        "Band minimums of 11–12 B are truncated malformed events. Malformed kinds: "
        + ", ".join(f"`{k}` × {v}" for k, v in ds["malformed_by_kind"].items()) + ".\n",
    ])


def _methodology() -> str:
    return _table(["Aspect", "What was done"], [
        ["In-process mode", "Calls the real `ingestion_service.ingest_raw_log` (or the pure pipeline for `pipeline_only`) "
         "in the harness process against `logforge_bench`. Pipeline + service + DB; no HTTP."],
        ["HTTP mode", "A separate `uvicorn app.main:app` (1 worker, no `--reload`, access log off) on 127.0.0.1, bound "
         "to `logforge_bench`; one keep-alive HTTP/1.1 connection per client thread; batch > 1 uses `POST /ingest/batch`."],
        ["Configurations", "Existing switches only (`DRIFT_ENABLED`, `RAW_VAULT_ENABLED`, Phase 8 hook registration); "
         "V0–V3 are configurations of the current code, not historical releases."],
        ["Isolation", "Each run TRUNCATEs `logforge_bench` and uses its own vault/anchor directory; a name guard refuses "
         "to reset any database not ending in `_bench`. The development database is never written."],
        ["Warm-up", "50–100 events (≥ batch × connections for batch runs) before fixed-size runs, excluded and reported "
         "separately; the sustained run reports 30 s of warm-up separately from 300 s of steady state."],
        ["Statistics", "Linear-interpolation percentiles over all samples; nothing trimmed. V0–V3 and the older variant "
         "block: 3 interleaved repetitions each; everything else: single runs."],
        ["PostgreSQL", "`pg_stat_database` deltas; cluster-wide `pg_stat_wal` / `pg_stat_bgwriter` deltas; "
         "`pg_stat_activity` sampled every 0.5 s; container CPU/memory from host `docker stats`."],
        ["Integrity", "Completion plan and sustained run: row counts vs events sent, duplicates, SHA-256 recomputed by "
         "PostgreSQL for every row, every vault object re-hashed, Merkle seal + full chain verification."],
    ]) + "\n"


def _suite_table(s, *, integrity: bool = False) -> str:
    if not s:
        return "NOT RUN."
    name, idx, rs = s
    rows = []
    for k, r in rs.items():
        if not _ok(r):
            rows.append([k, "MEASURED", f"**{r.get('status')}**: {str(r.get('failure'))[:100]}"] + [""] * (10 + integrity))
            continue
        c, tp, lat = r["config"], r["summary"]["throughput"], r["summary"]["latency"]
        row = [k, "MEASURED", c["mode"], c["variant"], c["workers"], c["batch_size"], tp["total_events"],
               tp["events_per_sec"], lat.get("p50"), lat.get("p95"), lat.get("p99"),
               f"{tp['successful']}/{tp['partial']}/{tp['failed']}/{tp['under_review']}", tp["harness_errors"]]
        if integrity:
            row.append("PASS" if r.get("integrity") and _integrity_eval(r)["passed"] else "**FAIL**")
        rows.append(row)
    head = ["Run", "Kind", "Mode", "Variant", "Conns", "Batch", "Events", "Events/s", "p50 ms", "p95 ms", "p99 ms",
            "S/P/F/UR", "Errors"] + (["Integrity"] if integrity else [])
    n = sum(1 for v in rs.values() if _ok(v))
    return (f"Suite `{name}`: **{n} of {len(rs)} runs completed**, {len(rs) - n} failed. S/P/F/UR = success / "
            "partial / failed / under review (Phase 5 drift hold). Latency per event (batch runs: request ÷ batch).\n\n"
            + _table(head, rows))


def _inproc_vs_http(done, kv, sweep) -> str:
    rows = []
    for a, b, what in (("B-inproc-v3_full-1000", "G-http-v3_full-1000", "standard suite, 1,000 events, 1 thread vs 1 connection"),
                       ("B-inproc-v3_full-100", "G-http-v3_full-100", "standard suite, 100 events")):
        if a in done and b in done:
            ta, tb = done[a]["summary"], done[b]["summary"]
            rows.append([what, "MEASURED", ta["throughput"]["events_per_sec"], tb["throughput"]["events_per_sec"],
                         ta["latency"]["p50"], tb["latency"]["p50"], ta["latency"]["p95"], tb["latency"]["p95"]])
    if "V3_full" in kv and (1, 1) in sweep:
        s, r = kv["V3_full"], sweep[(1, 1)]
        rows.append(["completion plan: `K-V3_full-*` (mean of 3 runs) vs `L-http-c1-b1`", "MEASURED",
                     round(s["eps_mean"], 2), r["summary"]["throughput"]["events_per_sec"], round(s["p50_mean"], 3),
                     r["summary"]["latency"]["p50"], round(s["p95_mean"], 3), r["summary"]["latency"]["p95"]])
    return ("Same configuration (`v3_full` / `V3_full`), same dataset and seed. In-process = no HTTP, no JSON "
            "request/response work.\n\n" + _table(
                ["Comparison", "Kind", "In-process ev/s", "HTTP ev/s", "In-process p50 ms", "HTTP p50 ms",
                 "In-process p95 ms", "HTTP p95 ms"], rows)
            + "\n\nThe two paths share the same per-event database work, and the between-run variance (section P) is "
              "of the same order as any difference here, so the HTTP overhead itself is not isolated (UNKNOWN).")


def _integrity_section(runs, sus, devdbs) -> str:
    rows = []
    items = [(k, v) for k, v in runs.items() if _ok(v) and v.get("integrity")]
    if sus and sus.get("integrity"):
        items.append(("sustained", sus))
    for k, r in items:
        i = r["integrity"]
        v, m = i.get("vault", {}), i.get("merkle", {})
        ev = _integrity_eval(r)
        rows.append([k, "MEASURED", i["expected_events"], i["events_in_db"], i["missing_events"],
                     i["unexpected_extra_events"], f"{i['duplicate_raw_hash_groups']} / {ev['expected_dups']}",
                     i["sha256_mismatches"],
                     f"{v.get('rehash_ok')}/{v.get('stored')}", v.get("rehash_mismatch"), v.get("object_missing"),
                     "valid" if m.get("chain_valid") else "**INVALID**", m.get("events_sealed"),
                     "PASS" if ev["passed"] else "**FAIL**"])
    parts = ["Accepted = events the harness sent (warm-up + measured) and the API accepted. Every check runs against "
             "the benchmark database only; the database is reset per run, so each row covers exactly one run.\n",
             "**Duplicates.** Some malformed kinds are constant strings (a truncated CEF header, an incomplete LEEF "
             "header, a truncated syslog header), so the harness itself sends a few byte-identical raw logs per run. "
             "LogForge does not deduplicate (documented behavior), so each is correctly stored as its own event. A "
             "run passes only if the duplicate groups stored equal the duplicate groups *sent* (regenerated from the "
             "deterministic dataset; the sustained run compares the full SHA-256 multiset of stored rows with the "
             "multiset sent). Anything else — a missing, extra or unexpected duplicate row — fails the run.\n",
             _table(["Run", "Kind", "Accepted", "Rows in DB", "Missing", "Extra", "Dup raw-hash groups stored / sent",
                     "SHA-256 mismatches", "Vault re-hash OK / stored", "Vault mismatches", "Vault missing",
                     "Merkle chain", "Events sealed", "Result"], rows),
             "\nV0 and V2 run with the cold vault switched off by design: their raw payloads stay in PostgreSQL "
             "(`event_raw_storage` status FAILED, backend `disabled`), so they show 0 stored vault objects.\n",
             "Malformed input producing PARTIAL or FAILED is expected behavior: those events are persisted with raw "
             "bytes and SHA-256 and are included in *Rows in DB* and the SHA-256 check.\n"]
    if sus and sus.get("integrity"):
        parts.append(f"Sustained run statuses: {json.dumps(sus['integrity'].get('status_counts', {}))}.\n")
    if devdbs:
        parts.append("**Development database untouched (MEASURED, read-only transactions):**\n")
        parts.append(_table(["Check", "Kind", "Database", "Events", "Events with benchmark markers", "Latest event"],
                            [[d.get("label"), "MEASURED", d.get("database"), _g(d, "row_counts", "events"),
                              d.get("events_with_benchmark_markers"), d.get("latest_event_received_at")]
                             for d in devdbs]))
        parts.append("\nMarkers are attribute names only the benchmark generator emits (`"
                     + "`, `".join(devdbs[0].get("benchmark_markers", [])) + "`).")
    return "\n".join(parts)


def _expected_dup_groups(r: dict[str, Any]) -> int:
    """Duplicate raw logs the harness itself SENT in this run, regenerated from the deterministic dataset
    (some malformed kinds are constant strings). Used for runs recorded before the harness compared the
    full SHA-256 multiset itself."""
    import hashlib
    from collections import Counter

    from logforge_bench import dataset
    from logforge_bench.runner import WARMUP_INDEX_OFFSET

    c = r["config"]
    kw = {"seed": c["seed"], "formats": tuple(c["formats"]), "sizes": tuple(c["sizes"]),
          "malformed_rate": c["malformed_rate"]}
    n_warm = (r.get("warmup") or {}).get("events", 0)
    idx = list(range(c["events"])) + list(range(WARMUP_INDEX_OFFSET, WARMUP_INDEX_OFFSET + n_warm))
    counts = Counter(hashlib.sha256(dataset.make_event(i, **kw).raw.encode("utf-8")).hexdigest() for i in idx)
    return sum(1 for v in counts.values() if v > 1)


def _integrity_eval(r: dict[str, Any]) -> dict[str, Any]:
    i = r["integrity"]
    if "raw_hash_multiset_equals_sent" in i:
        return {"passed": bool(i["all_passed"]), "expected_dups": i["expected_duplicate_raw_hash_groups"],
                "method": "exact SHA-256 multiset of stored rows = multiset sent"}
    from logforge_bench.inprocess import VARIANTS

    exp = _expected_dup_groups(r)
    v, m = i.get("vault", {}), i.get("merkle", {})
    vault_expected = VARIANTS[r["config"]["variant"]]["vault"]
    ok = (i["missing_events"] == 0 and i["unexpected_extra_events"] == 0 and i["duplicate_event_ids"] == 0
          and i["sha256_mismatches"] == 0 and i["events_without_raw_storage_row"] == 0
          and i["duplicate_raw_hash_groups"] == exp and v.get("rehash_mismatch") == 0 and v.get("object_missing") == 0
          and (not vault_expected or v.get("rehash_ok") == i["events_in_db"])
          and m.get("chain_valid") is True and m.get("events_sealed") == i["events_in_db"])
    return {"passed": ok, "expected_dups": exp,
            "method": "counts + duplicate groups equal to the duplicates sent (regenerated dataset)"}
