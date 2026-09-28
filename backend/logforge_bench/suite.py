"""The standard benchmark matrix. Writes every run into one new suite
directory plus `suite.json` (an index of all runs, failed ones included)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logforge_bench import dataset
from logforge_bench.runner import Context, RunConfig, execute

LOAD_LEVELS = (1, 100, 1_000, 10_000)
VARIANT_ORDER = ("pipeline_only", "core_nodrift", "v0_core", "v1_phase7", "v3_full")


def plan(*, quick: bool, max_events: int, reps: int, seed: int, skip_http: bool) -> list[RunConfig]:
    def n(x: int) -> tuple[int, str | None]:
        want = max(1, x // 10) if quick and x > 100 else x
        if want > max_events:
            return max_events, f"capped from {want} by --max-events {max_events}"
        return want, None

    def cfg(label: str, events: int, **kw) -> RunConfig:
        ev, cap = n(events)
        return RunConfig(label=label, events=ev, seed=seed, cap_reason=cap, **kw)

    runs: list[RunConfig] = []
    # A/B — load levels, in-process
    for level in LOAD_LEVELS:
        runs.append(cfg(f"A-inproc-pipeline_only-{level}", level, mode="inprocess", variant="pipeline_only",
                        warmup_events=0 if level == 1 else 50))
    for level in LOAD_LEVELS:
        runs.append(cfg(f"B-inproc-v3_full-{level}", level, mode="inprocess", variant="v3_full",
                        warmup_events=0 if level == 1 else 50, jobs=(level == LOAD_LEVELS[-1])))
    # C — feature-isolated variants, interleaved (rotated order per repetition)
    for rep in range(1 if quick else reps):
        order = VARIANT_ORDER[rep % len(VARIANT_ORDER):] + VARIANT_ORDER[:rep % len(VARIANT_ORDER)]
        for v in order:
            runs.append(cfg(f"C-variant-{v}-rep{rep + 1}", 1_000, mode="inprocess", variant=v, warmup_events=100,
                            extra={"group": "variants", "rep": rep + 1}))
    # D — malformed-rate sweep (5% is covered by C)
    for rate in (0.0, 0.25, 0.50):
        runs.append(cfg(f"D-malformed-{int(rate * 100)}pct", 1_000, mode="inprocess", variant="v3_full",
                        malformed_rate=rate, extra={"group": "malformed"}))
    # E — size-band isolation (one band at a time, all formats)
    for band in dataset.SIZE_BANDS:
        runs.append(cfg(f"E-size-{band}", 600, mode="inprocess", variant="v3_full", sizes=(band,),
                        extra={"group": "size"}))
    # F — in-process threads
    runs.append(cfg("F-inproc-v3_full-workers4", 2_000, mode="inprocess", variant="v3_full", workers=4,
                    extra={"group": "concurrency"}))
    if skip_http:
        return runs
    # G — HTTP load levels
    for level in LOAD_LEVELS:
        runs.append(cfg(f"G-http-v3_full-{level}", level, mode="http", variant="v3_full",
                        workers=1 if level < 10_000 else 4, warmup_events=0 if level == 1 else 50))
    # H — HTTP concurrency sweep
    for w in (2, 4, 8):
        runs.append(cfg(f"H-http-v3_full-workers{w}", 1_000 * max(1, w // 2), mode="http", variant="v3_full",
                        workers=w, extra={"group": "concurrency"}))
    # I — HTTP batch endpoint
    for w in (1, 4):
        runs.append(cfg(f"I-http-batch100-workers{w}", 2_000, mode="http", variant="v3_full", batch_size=100,
                        workers=w, warmup_events=100, extra={"group": "batch"}))
    # J — HTTP variants (interleaved)
    for rep in range(1 if quick else 2):
        for v in (("v0_core", "v1_phase7", "v3_full") if rep % 2 == 0 else ("v3_full", "v1_phase7", "v0_core")):
            runs.append(cfg(f"J-http-variant-{v}-rep{rep + 1}", 1_000, mode="http", variant=v, workers=4,
                            extra={"group": "http_variants", "rep": rep + 1}))
    return runs


V_ORDER = ("V0_core", "V1_core_phase7", "V2_core_drift", "V3_full")


def plan_completion(*, quick: bool, reps: int, seed: int) -> list[RunConfig]:
    """Step 10 completion: the four named configurations V0-V3 interleaved
    (rotated order per repetition) and the HTTP connections x batch-size
    sweep. Every run collects PostgreSQL activity and ends with the full
    integrity verification."""
    n = (lambda x: max(1, x // 10)) if quick else (lambda x: x)  # noqa: E731
    runs: list[RunConfig] = []
    for rep in range(1 if quick else reps):
        k = rep % len(V_ORDER)
        for v in V_ORDER[k:] + V_ORDER[:k]:
            runs.append(RunConfig(label=f"K-{v}-rep{rep + 1}", events=n(1_000), seed=seed, mode="inprocess", variant=v,
                                  warmup_events=100, integrity=True, extra={"group": "V0-V3", "rep": rep + 1}))
    for batch in (1, 50, 100):
        for conns in (1, 2, 4, 8):
            runs.append(RunConfig(label=f"L-http-c{conns}-b{batch}", events=n(2_000), seed=seed, mode="http",
                                  variant="V3_full", workers=conns, batch_size=batch,
                                  warmup_events=max(50, batch * conns), integrity=True,
                                  extra={"group": "http_sweep", "connections": conns, "batch": batch}))
    return runs


def plan_scaling(*, quick: bool, seed: int, target_url: str, replicas: int, combos: str) -> list[RunConfig]:
    """Step 10B: the Step 10 HTTP workload (same dataset, seed, 2,000 events, warm-up rule, V3_full = default
    configuration, scheduler off) against the nginx load balancer in front of `replicas` API replicas."""
    runs = []
    for combo in combos.split(","):
        batch, conns = (int(x) for x in combo.lower().split("x"))
        runs.append(RunConfig(label=f"S-r{replicas}-c{conns}-b{batch}", events=200 if quick else 2_000, seed=seed,
                              mode="http", variant="V3_full", workers=conns, batch_size=batch,
                              warmup_events=max(50, batch * conns), integrity=True, target_url=target_url,
                              replicas=replicas, extra={"group": "scaling", "replicas": replicas,
                                                        "connections": conns, "batch": batch}))
    return runs


def run_suite(ctx: Context, *, quick: bool, max_events: int, reps: int, seed: int, skip_http: bool,
              which: str = "standard", target_url: str | None = None, replicas: int | None = None,
              combos: str = "") -> int:
    started = datetime.now(tz=timezone.utc)
    tag = f"-{which}" + (f"-r{replicas}" if which == "scaling" else "") if which != "standard" else ""
    suite_dir = ctx.results_root / (started.strftime("suite-%Y%m%dT%H%M%SZ") + tag + ("-quick" if quick else ""))
    suite_dir.mkdir(parents=True, exist_ok=False)
    ctx.results_root = suite_dir
    if which == "scaling":
        if not target_url or not replicas:
            raise SystemExit("--plan scaling needs --target-url and --replicas")
        runs = plan_scaling(quick=quick, seed=seed, target_url=target_url, replicas=replicas, combos=combos)
    elif which == "completion":
        runs = plan_completion(quick=quick, reps=reps, seed=seed)
    else:
        runs = plan(quick=quick, max_events=max_events, reps=reps, seed=seed, skip_http=skip_http)
    index: dict[str, Any] = {"suite": suite_dir.name, "plan": which, "quick": quick, "max_events": max_events, "seed": seed,
                             "started_utc": started.isoformat(), "planned_runs": len(runs), "runs": []}
    print(f"[bench] suite {suite_dir.name}: {len(runs)} runs", flush=True)
    failures = 0
    for i, cfg in enumerate(runs, 1):
        print(f"[bench] ({i}/{len(runs)}) {cfg.label} ...", flush=True)
        out = execute(ctx, cfg)
        from logforge_bench.cli import _print_result

        _print_result(out)
        failures += out["status"] != "COMPLETED"
        index["runs"].append({"label": cfg.label, "status": out["status"], "dir": Path(out["run_dir"]).name,
                              "failure": out.get("failure")})
        _write_index(suite_dir, index)
    index["finished_utc"] = datetime.now(tz=timezone.utc).isoformat()
    index["failed_runs"] = failures
    _write_index(suite_dir, index)
    print(f"[bench] suite finished: {len(runs) - failures} completed, {failures} failed -> {suite_dir}", flush=True)
    return 0 if failures == 0 else 1


def _write_index(suite_dir: Path, index: dict[str, Any]) -> None:
    (suite_dir / "suite.json").write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
