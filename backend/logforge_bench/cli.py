"""Command line: python -m logforge_bench <command> [options]

  dataset    describe (or dump) the deterministic dataset
  run        one benchmark run (in-process or HTTP)
  sustained  one sustained run (default 300 s steady state after a 30 s warm-up)
  suite      the standard matrix (load levels, variants, malformed sweep, HTTP concurrency/batching)
  report     render the Markdown report from a suite directory (+ optional sustained run)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from logforge_bench import BENCH_VERSION, dataset

DEFAULT_RESULTS = Path(__file__).resolve().parent / "results"
DEFAULT_WORKDIR = Path("/tmp/logforge-bench")


def _csv(value: str, allowed) -> tuple[str, ...]:
    items = tuple(v.strip() for v in value.split(",") if v.strip())
    bad = [v for v in items if v not in allowed]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown value(s) {bad}; allowed: {list(allowed)}")
    return items


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--format", dest="formats", default=",".join(dataset.FORMATS),
                   type=lambda v: _csv(v, dataset.FORMATS), help="comma list of " + ",".join(dataset.FORMATS))
    p.add_argument("--size", dest="sizes", default=",".join(dataset.SIZE_BANDS),
                   type=lambda v: _csv(v, dataset.SIZE_BANDS), help="comma list of " + ",".join(dataset.SIZE_BANDS))
    p.add_argument("--malformed-rate", type=float, default=dataset.DEFAULT_MALFORMED_RATE)


def _run_opts(p: argparse.ArgumentParser) -> None:
    from logforge_bench.inprocess import VARIANTS

    _common(p)
    p.add_argument("--mode", choices=("inprocess", "http"), default="inprocess")
    p.add_argument("--variant", choices=tuple(VARIANTS), default="v3_full")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--scheduler", action="store_true", help="HTTP: run the server's background scheduler")
    p.add_argument("--label", default="")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--no-reset", action="store_true", help="keep existing rows in the benchmark database")
    p.add_argument("--integrity", action="store_true",
                   help="after the run: counts, duplicates, SQL SHA-256, vault re-hash, Merkle seal + chain verify")
    p.add_argument("--target-url", default=None,
                   help="Step 10B: benchmark an already-running endpoint (e.g. http://lb:8080) instead of starting a server")
    p.add_argument("--replicas", type=int, default=None, help="Step 10B: number of API replicas behind --target-url")
    p.add_argument("--statelessness", action="store_true",
                   help="Step 10B: after the run, read back + integrity-verify a sample of events through the LB")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m logforge_bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=BENCH_VERSION)
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dataset", help="describe the deterministic dataset")
    _common(d)
    d.add_argument("--events", type=int, default=1000)
    d.add_argument("--dump", type=Path, help="write the raw events as JSONL")

    r = sub.add_parser("run", help="one benchmark run")
    _run_opts(r)
    r.add_argument("--events", type=int, default=1000)
    r.add_argument("--warmup", type=int, default=50, help="warm-up events (excluded from the measurement)")
    r.add_argument("--jobs", action="store_true", help="measure off-path jobs (Phase 8 drift, shadow, seal) afterwards")

    s = sub.add_parser("sustained", help="one sustained run")
    _run_opts(s)
    s.add_argument("--duration", type=float, default=300.0, help="steady-state seconds (after the warm-up)")
    s.add_argument("--warmup-seconds", type=float, default=30.0)
    s.add_argument("--window", type=float, default=10.0)

    q = sub.add_parser("suite", help="standard matrix")
    q.add_argument("--quick", action="store_true", help="smoke-sized matrix (validates the harness, not the system)")
    q.add_argument("--max-events", type=int, default=10_000, help="cap for any single run")
    q.add_argument("--reps", type=int, default=3, help="repetitions of the variant comparison")
    q.add_argument("--seed", type=int, default=1337)
    q.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    q.add_argument("--skip-http", action="store_true")
    q.add_argument("--plan", choices=("standard", "completion", "scaling"), default="standard",
                   help="standard = the 46-run matrix; completion = V0-V3 block + HTTP connections x batch sweep; "
                        "scaling = Step 10B sweep against --target-url for one replica count")
    q.add_argument("--target-url", default=None)
    q.add_argument("--replicas", type=int, default=None)
    q.add_argument("--combos", default="1x1,1x4,1x8,1x16,50x4,50x16,100x4,100x16",
                   help="scaling plan: comma list of <batch>x<connections>")

    rep = sub.add_parser("report", help="render Markdown")
    rep.add_argument("--suite", type=Path, required=True, action="append", help="repeatable")
    rep.add_argument("--sustained", type=Path, help="result.json of the sustained run")
    rep.add_argument("--out", type=Path, required=True)
    rep.add_argument("--docker-stats", type=Path, action="append", default=[],
                     help="docker_stats-*.jsonl written by scripts/bench.sh (repeatable)")
    rep.add_argument("--devdb", type=Path, action="append", default=[], help="devdb-check JSON files (repeatable)")
    rep.add_argument("--junit", type=Path, action="append", default=[],
                     help="JUnit XML test reports to summarize (repeatable; name=path to label, e.g. backend=...)")

    dv = sub.add_parser("devdb-check", help="read-only evidence that the application database holds no benchmark data")
    dv.add_argument("--out", type=Path, required=True)
    dv.add_argument("--label", default="")
    return ap


# Strings that occur only in benchmark-generated events (padding attribute names of logforge_bench.dataset).
BENCH_MARKERS = ("cfgattr000=", "flexString000=", "customAttr000=", "ext_attr_000", "<Attr000>", " pad000=")


def devdb_check(out: Path, label: str) -> dict:
    """Read-only (SET TRANSACTION READ ONLY) scan of the APPLICATION database."""
    from sqlalchemy import create_engine, text

    from logforge_bench import environment

    url = environment.app_database_url()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            name = conn.execute(text("SELECT current_database()")).scalar()
            if name.endswith(environment.BENCH_SUFFIX):
                raise SystemExit("devdb-check must target the application database, not the benchmark database")
            tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")).scalars().all()
            counts = {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in tables}
            like = " OR ".join(f"raw_event LIKE :m{i}" for i in range(len(BENCH_MARKERS)))
            params = {f"m{i}": f"%{m}%" for i, m in enumerate(BENCH_MARKERS)}
            markers = conn.execute(text(f"SELECT count(*) FROM events WHERE {like}"), params).scalar()
            latest = conn.execute(text("SELECT max(received_at) FROM events")).scalar()
            conn.rollback()
    finally:
        engine.dispose()
    result = {"label": label, "checked_utc": datetime.now(tz=timezone.utc).isoformat(), "database": name,
              "read_only_transaction": True, "row_counts": counts, "events_with_benchmark_markers": markers,
              "benchmark_markers": list(BENCH_MARKERS), "latest_event_received_at": str(latest)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def _context(results: Path, *, scheduler: bool = False):
    from logforge_bench import environment

    app_url = environment.app_database_url()
    bench_url = environment.bench_database_url(app_url)
    DEFAULT_WORKDIR.mkdir(parents=True, exist_ok=True)
    workdir = DEFAULT_WORKDIR / datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    workdir.mkdir(parents=True)
    environment.configure_process_env(bench_url, workdir, scheduler=scheduler)
    info = environment.ensure_bench_database(app_url, bench_url)
    from logforge_bench.runner import Context

    results.mkdir(parents=True, exist_ok=True)
    ctx = Context(results, workdir, bench_url)
    print(f"[bench] benchmark database: {info['database']} (created={info['created']}); results: {results}", flush=True)
    return ctx


def _cfg_from(args, **over):
    from logforge_bench.runner import RunConfig

    base = dict(mode=args.mode, variant=args.variant, seed=args.seed, formats=args.formats, sizes=args.sizes,
                malformed_rate=args.malformed_rate, workers=args.workers, batch_size=args.batch_size,
                scheduler=args.scheduler, label=args.label, reset_db=not args.no_reset, integrity=args.integrity,
                target_url=args.target_url, replicas=args.replicas,
                extra={"statelessness": True} if args.statelessness else {})
    base.update(over)
    return RunConfig(**base)


def _print_result(out: dict) -> None:
    s = out.get("summary", {})
    tp, lat = s.get("throughput", {}), s.get("latency", {})
    print(f"[bench] {out.get('label') or out['config']['variant']} {out['config']['mode']}: {out['status']} "
          f"events={tp.get('total_events')} eps={tp.get('events_per_sec')} p50={lat.get('p50')} "
          f"p95={lat.get('p95')} p99={lat.get('p99')} ms -> {out.get('run_dir')}", flush=True)
    if out.get("failure"):
        print(f"[bench]   FAILURE: {out['failure']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "dataset":
        events = dataset.generate(args.events, seed=args.seed, formats=args.formats, sizes=args.sizes,
                                  malformed_rate=args.malformed_rate)
        print(json.dumps(dataset.describe(events), indent=2))
        if args.dump:
            with open(args.dump, "w", encoding="utf-8") as fh:
                for e in events:
                    fh.write(json.dumps(e.__dict__) + "\n")
        return 0
    if args.command == "report":
        from logforge_bench import report

        report.render(args.suite, args.sustained, args.out, args.docker_stats, devdb=args.devdb, junit=args.junit)
        print(f"[bench] report written to {args.out}")
        return 0
    if args.command == "devdb-check":
        r = devdb_check(args.out, args.label)
        print(f"[bench] {r['database']}: events={r['row_counts'].get('events')} "
              f"benchmark-marker events={r['events_with_benchmark_markers']} -> {args.out}")
        return 0
    if args.command == "suite":
        from logforge_bench import suite

        ctx = _context(args.results)
        return suite.run_suite(ctx, quick=args.quick, max_events=args.max_events, reps=args.reps, seed=args.seed,
                               skip_http=args.skip_http, which=args.plan, target_url=args.target_url,
                               replicas=args.replicas, combos=args.combos)

    ctx = _context(args.results)
    from logforge_bench import runner

    if args.command == "run":
        cfg = _cfg_from(args, events=args.events, warmup_events=args.warmup, jobs=args.jobs)
    else:
        cfg = _cfg_from(args, duration_s=args.duration, warmup_seconds=args.warmup_seconds, window_s=args.window,
                        warmup_events=0, label=args.label or "sustained")
    out = runner.execute(ctx, cfg)
    _print_result(out)
    if out.get("sustained"):
        print(f"[bench] sustained verdict: {out['sustained'].get('verdict')}", flush=True)
    return 0 if out["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
