"""One benchmark run = reset the benchmark DB, warm up, measure, verify,
write artifacts. Every run writes into its own new directory; nothing
existing is overwritten."""
from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logforge_bench import analysis, dataset, metrics

WARMUP_INDEX_OFFSET = 900_000_000  # warm-up events never share raw bytes with measured events


@dataclass
class RunConfig:
    mode: str = "inprocess"            # inprocess | http
    variant: str = "v3_full"
    events: int = 1000
    duration_s: float | None = None    # sustained: run for this long instead of a fixed count
    seed: int = 1337
    formats: tuple[str, ...] = dataset.FORMATS
    sizes: tuple[str, ...] = tuple(dataset.SIZE_BANDS)
    malformed_rate: float = dataset.DEFAULT_MALFORMED_RATE
    workers: int = 1
    batch_size: int = 1
    warmup_events: int = 50
    warmup_seconds: float = 30.0       # sustained only: reported separately from steady state
    window_s: float = 10.0             # sustained only
    reset_db: bool = True
    scheduler: bool = False            # HTTP server background scheduler
    scheduler_interval_s: int = 60
    jobs: bool = False                 # run the off-path jobs afterwards (inprocess, persisting variants)
    integrity: bool = False            # full post-run integrity verification of the benchmark DB
    target_url: str | None = None      # Step 10B: benchmark an external endpoint (nginx -> N replicas)
    replicas: int | None = None        # Step 10B: number of API replicas behind target_url (recorded)
    label: str = ""
    cap_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def dataset_kwargs(self) -> dict[str, Any]:
        return {"seed": self.seed, "formats": self.formats, "sizes": self.sizes, "malformed_rate": self.malformed_rate}


class Context:
    """Process-wide state: results root, bench DB engine, workdir."""

    def __init__(self, results_root: Path, workdir: Path, bench_url: str):
        self.results_root = results_root
        self.workdir = workdir
        self.bench_url = bench_url
        from app.db.base import engine

        self.engine = engine


def _new_run_dir(root: Path, label: str) -> tuple[str, Path]:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    d = root / f"{run_id}{'-' + label if label else ''}"
    d.mkdir(parents=True, exist_ok=False)
    return run_id, d


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")


def _verify_persistence(before: dict, after: dict, cfg: RunConfig, sent: int) -> dict[str, Any]:
    from logforge_bench.inprocess import VARIANTS

    spec = VARIANTS[cfg.variant]
    if not spec["persist"]:
        return {"applicable": False}
    added = {t: after["row_counts"][t] - before["row_counts"][t] for t in after["row_counts"]}
    checks = {"event_rows_added_equals_events_sent": added["events"] == sent,
              "raw_storage_rows_added_equals_events_sent": added["event_raw_storage"] == sent}
    if spec["phase8"]:
        checks["compact_lineage_rows_added_equals_events_sent"] = added["event_lineage_compact"] == sent
    return {"applicable": True, "events_sent": sent, "rows_added": added, "checks": checks,
            "all_passed": all(checks.values())}


def sent_hash_counts(records: list[dict[str, Any]], cfg: "RunConfig", warmup_events: int) -> dict[str, int]:
    """SHA-256 multiset of every raw log the harness sent (measured + warm-up), regenerated from the
    deterministic dataset. Some malformed kinds are constant strings (e.g. a truncated CEF header), so the
    same raw log can legitimately be sent — and stored — more than once; LogForge does no deduplication."""
    import hashlib
    from collections import Counter

    kw = cfg.dataset_kwargs()
    idx = [r["i"] for r in records] + list(range(WARMUP_INDEX_OFFSET, WARMUP_INDEX_OFFSET + warmup_events))
    return dict(Counter(hashlib.sha256(dataset.make_event(i, **kw).raw.encode("utf-8")).hexdigest() for i in idx))


def split_sent(records: list[dict[str, Any]], cfg: "RunConfig", warmup_events: int) -> tuple[dict[str, int], dict[str, int]]:
    """(acknowledged, ambiguous) SHA-256 multisets. Acknowledged = the API answered 2xx (must be stored
    exactly once). Ambiguous = the request failed (e.g. the replica was killed mid-request): the event may
    or may not have been committed before the failure, so 0 or 1 stored copies are both consistent."""
    ok = [r for r in records if not r.get("error")]
    bad = [r for r in records if r.get("error")]
    return sent_hash_counts(ok, cfg, warmup_events), sent_hash_counts(bad, cfg, 0)


def integrity_check(ctx: Context, expected_events: int, *, vault_expected: bool,
                    sent_hashes: dict[str, int] | None = None,
                    ambiguous_hashes: dict[str, int] | None = None) -> dict[str, Any]:
    """Post-run verification of everything the run stored (the benchmark DB is
    reset per run, so it holds exactly this run's warm-up + measured events):
    counts, duplicates, SHA-256 recomputed by PostgreSQL, every cold-vault
    object re-hashed, then Merkle sealing + full chain verification."""
    from sqlalchemy import text

    from app.db.base import SessionLocal
    from app.evidence.raw_vault import VaultError, get_vault
    from app.services import evidence_service

    t0 = time.perf_counter()
    out: dict[str, Any] = {"expected_events": expected_events}
    with ctx.engine.connect() as conn:
        q = lambda sql: conn.execute(text(sql)).scalar()  # noqa: E731
        total = q("SELECT count(*) FROM events")
        out["events_in_db"] = total
        out["missing_events"] = max(expected_events - total, 0)
        out["unexpected_extra_events"] = max(total - expected_events, 0)
        out["status_counts"] = dict(conn.execute(text("SELECT status, count(*) FROM events GROUP BY 1 ORDER BY 1")).all())
        out["duplicate_raw_hash_groups"] = q("SELECT count(*) FROM (SELECT raw_hash FROM events GROUP BY raw_hash "
                                             "HAVING count(*) > 1) d")
        out["duplicate_event_ids"] = q("SELECT count(*) - count(DISTINCT event_id) FROM events")
        if sent_hashes is not None:
            stored = dict(conn.execute(text("SELECT raw_hash, count(*) FROM events GROUP BY raw_hash")).all())
            amb = ambiguous_hashes or {}
            out["expected_duplicate_raw_hash_groups"] = sum(1 for n in sent_hashes.values() if n > 1)
            out["raw_hashes_missing"] = sum(max(n - stored.get(h, 0), 0) for h, n in sent_hashes.items())
            out["raw_hashes_unexpected"] = sum(max(n - sent_hashes.get(h, 0) - amb.get(h, 0), 0)
                                               for h, n in stored.items())
            out["raw_hash_multiset_equals_sent"] = stored == sent_hashes
            if amb:
                out["ambiguous_requests_events"] = sum(amb.values())
                out["ambiguous_events_stored"] = sum(min(max(stored.get(h, 0) - sent_hashes.get(h, 0), 0), n)
                                                     for h, n in amb.items())
                out["ambiguous_events_not_stored"] = out["ambiguous_requests_events"] - out["ambiguous_events_stored"]
                # consistent = every acknowledged event stored once, nothing beyond acknowledged + ambiguous
                out["raw_hash_multiset_consistent"] = out["raw_hashes_missing"] == 0 and out["raw_hashes_unexpected"] == 0
                out["missing_events"] = out["raw_hashes_missing"]
                out["unexpected_extra_events"] = out["raw_hashes_unexpected"]
        out["sha256_mismatches"] = q("SELECT count(*) FROM events WHERE "
                                     "encode(sha256(convert_to(raw_event, 'UTF8')), 'hex') <> raw_hash")
        out["events_without_raw_storage_row"] = q("SELECT count(*) FROM events e LEFT JOIN event_raw_storage r "
                                                  "ON r.event_id = e.event_id WHERE r.event_id IS NULL")
        rows = conn.execute(text("SELECT r.backend, r.object_key, r.sha256, e.raw_hash, r.status FROM event_raw_storage r "
                                 "JOIN events e ON e.event_id = r.event_id")).all()
    stored = [r for r in rows if r[4] == "STORED"]
    ok = bad = missing = 0
    for backend, key, sha, raw_hash, _ in stored:
        try:
            vault = get_vault(backend)
            if not vault.exists(key):
                missing += 1
            elif vault.verify(key, sha) and sha == raw_hash:
                ok += 1
            else:
                bad += 1
        except VaultError:
            bad += 1
    out["vault"] = {"expected_cold_copies": vault_expected, "storage_rows": len(rows), "stored": len(stored),
                    "rehash_ok": ok, "rehash_mismatch": bad, "object_missing": missing,
                    "not_stored_by_status": dict(__import__("collections").Counter(r[4] for r in rows if r[4] != "STORED"))}
    with SessionLocal() as db:
        try:
            sealed = evidence_service.seal(db, force=True, max_batches=1000)
            chain = evidence_service.verify_chain(db)
            out["merkle"] = {"batches_sealed_now": len(sealed), "chain_valid": chain["valid"],
                             "batches_checked": chain["batches_checked"], "events_sealed": chain["events_sealed"],
                             "problems": chain["problems"][:10]}
        except Exception as exc:  # noqa: BLE001 — recorded as a failed check
            db.rollback()
            out["merkle"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "chain_valid": False}
    m = out["merkle"]
    out["all_passed"] = bool(
        out["missing_events"] == 0 and out["unexpected_extra_events"] == 0
        and (out.get("raw_hash_multiset_consistent", out.get("raw_hash_multiset_equals_sent")) if sent_hashes is not None
             else out["duplicate_raw_hash_groups"] == 0)
        and out["duplicate_event_ids"] == 0 and out["sha256_mismatches"] == 0 and out["events_without_raw_storage_row"] == 0
        and bad == 0 and missing == 0 and (not vault_expected or ok == total)
        and m.get("chain_valid") and m.get("events_sealed") == total)
    out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _settle_stats() -> None:
    time.sleep(1.2)  # PostgreSQL flushes backend statistics asynchronously (~1 s)


def _clear_shared_evidence(root: Path) -> None:
    import shutil

    for sub in ("raw_vault", "anchors"):
        p = root / sub
        if p.exists():
            for child in p.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, onerror=lambda f, path, e: (os.chmod(path, 0o755), f(path)))
                else:
                    os.chmod(child, 0o644)
                    child.unlink()


def execute(ctx: Context, cfg: RunConfig) -> dict[str, Any]:
    from logforge_bench import environment, inprocess

    run_id, run_dir = _new_run_dir(ctx.results_root, cfg.label)
    started = datetime.now(tz=timezone.utc)
    reset_tables = environment.reset_bench_database(ctx.engine) if cfg.reset_db else []
    out: dict[str, Any] = {"run_id": run_id, "label": cfg.label, "config": asdict(cfg), "started_utc": started.isoformat(),
                           "mode_label": "IN-PROCESS (pipeline/service cost, no HTTP)" if cfg.mode == "inprocess"
                           else "HTTP/API (real uvicorn process, client in same container)",
                           "db_reset": bool(reset_tables)}
    server = None
    timer = None
    # Per-run evidence storage: the DB (and so the Merkle sequence) restarts per run, so the
    # write-once anchor store and the content-addressed vault must too.
    shared = os.environ.get("LOGFORGE_BENCH_SHARED_EVIDENCE") if cfg.target_url else None
    if shared:
        # Step 10B: every replica writes to ONE shared vault/anchor volume (their paths are fixed at container
        # start), so it is emptied together with the database at the start of each run.
        evidence_dir = Path(shared)
        if cfg.reset_db:
            _clear_shared_evidence(evidence_dir)
    else:
        evidence_dir = ctx.workdir / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    from app.config import get_settings

    get_settings().raw_vault_path = str(evidence_dir / "raw_vault")
    get_settings().evidence_anchor_path = str(evidence_dir / "anchors")
    out["evidence_dir"] = str(evidence_dir)
    try:
        if cfg.mode == "inprocess":
            out["variant_applied"] = inprocess.apply_variant(cfg.variant)
            timer = inprocess.StageTimer()
        else:
            from logforge_bench import http_mode

            if cfg.target_url:
                server = http_mode.ExternalTarget(cfg.target_url, replicas=cfg.replicas, variant=cfg.variant,
                                                  scheduler=cfg.scheduler).__enter__()
                out["mode_label"] = (f"HTTP/API through nginx round-robin -> {cfg.replicas} replica(s) "
                                     "(load generator in its own container)")
            else:
                server = http_mode.Server(bench_url=ctx.bench_url, workdir=evidence_dir, variant=cfg.variant,
                                          scheduler=cfg.scheduler, scheduler_interval=cfg.scheduler_interval_s).__enter__()
            out["server"] = server.config

        # Warm-up (excluded from the measurement, recorded separately).
        if cfg.warmup_events and not cfg.duration_s:
            warm = dataset.generate(cfg.warmup_events, start=WARMUP_INDEX_OFFSET, **cfg.dataset_kwargs())
            w0 = time.perf_counter()
            if cfg.mode == "inprocess":
                wres = inprocess.run(warm, variant=cfg.variant, workers=cfg.workers, batch_size=cfg.batch_size)
            else:
                wres = http_mode.run(server, warm, workers=cfg.workers, batch_size=cfg.batch_size)
            out["warmup"] = {"events": len(warm), "wall_s": round(time.perf_counter() - w0, 3),
                             "latency": metrics.latency_summary([r["latency_ms"] for r in wres["records"]])}

        pids = {"harness": "self"} if server is None or not server.pid else {"harness": "self", "server": server.pid}
        _settle_stats()
        pg_before = metrics.pg_snapshot(ctx.engine)
        if timer:
            timer.install()
        out["measure_start_utc"] = datetime.now(tz=timezone.utc).isoformat()
        sampler = metrics.ResourceSampler(pids, interval=1.0).start()
        pg_sampler = metrics.PgActivitySampler(ctx.engine).start()
        if cfg.duration_s:
            res = _run_sustained(ctx, cfg, server)
        else:
            events = dataset.generate(cfg.events, **cfg.dataset_kwargs())
            out["dataset"] = dataset.describe(events)
            if cfg.mode == "inprocess":
                res = inprocess.run(events, variant=cfg.variant, workers=cfg.workers, batch_size=cfg.batch_size)
            else:
                res = http_mode.run(server, events, workers=cfg.workers, batch_size=cfg.batch_size)
        resources = sampler.stop()
        out["pg_activity"] = pg_sampler.stop()
        out["measure_end_utc"] = datetime.now(tz=timezone.utc).isoformat()
        if cfg.target_url:
            from logforge_bench import scale

            a = datetime.fromisoformat(out["measure_start_utc"]).timestamp()
            b = datetime.fromisoformat(out["measure_end_utc"]).timestamp()
            time.sleep(1.0)  # let nginx flush the last access-log lines
            out["load_balancer"] = scale.lb_window(a, b + 1)
            out["client_upstreams"] = scale.client_distribution(res["requests"])
            out["failure_events"] = scale.failure_events(a, b)
        if timer:
            timer.uninstall()
        _settle_stats()
        pg_after = metrics.pg_snapshot(ctx.engine)
        records = res["records"]
        if cfg.duration_s:
            out["dataset"] = res.get("dataset")
        out["summary"] = analysis.summarize(records, res["wall_s"])
        out["by_format"] = analysis.breakdown(records, "format")
        out["by_size_band"] = analysis.breakdown(records, "size_band")
        if cfg.mode == "http":
            out["http"] = http_mode.request_summary(res["requests"])
            _write_jsonl(run_dir / "requests.jsonl", res["requests"])
        if timer:
            out["stages"] = timer.summary(len(records))
        out["resources"] = resources
        out["database"] = metrics.pg_delta(pg_before, pg_after, len(records), res["wall_s"])
        out["verification"] = _verify_persistence(pg_before, pg_after, cfg, len(records))
        if cfg.integrity and inprocess.VARIANTS[cfg.variant]["persist"]:
            warm_n = (out.get("warmup") or {}).get("events", 0)
            acked, ambiguous = split_sent(records, cfg, warm_n)
            out["integrity"] = integrity_check(ctx, len(records) + warm_n,
                                               vault_expected=inprocess.VARIANTS[cfg.variant]["vault"],
                                               sent_hashes=acked, ambiguous_hashes=ambiguous)
        if cfg.target_url and cfg.extra.get("statelessness"):
            import hashlib

            from logforge_bench import scale

            kw = cfg.dataset_kwargs()
            for r in records:
                r["raw_hash_sent"] = hashlib.sha256(dataset.make_event(r["i"], **kw).raw.encode("utf-8")).hexdigest()
            out["statelessness"] = scale.statelessness_check(cfg.target_url, records)
        if cfg.duration_s:
            out["sustained"] = res["sustained"]
            finalize_sustained(out, cfg, sampler.samples)
        if cfg.jobs and cfg.mode == "inprocess" and inprocess.VARIANTS[cfg.variant]["persist"]:
            from app.db.base import SessionLocal
            from logforge_bench import jobs

            with SessionLocal() as db:
                lo, hi = jobs.received_range(db)
                out["jobs"] = jobs.run_all(db, t_first=lo, t_last=hi, events=pg_after["row_counts"]["events"])
        _write_jsonl(run_dir / "events.jsonl", records)
        _write_jsonl(run_dir / "resource_samples.jsonl", sampler.samples)
        out["status"] = "COMPLETED"
    except BaseException as exc:  # a failed run is recorded, never hidden
        out["status"] = "FAILED"
        out["failure"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
        if timer:
            timer.uninstall()
        if isinstance(exc, KeyboardInterrupt):
            _write_json(run_dir / "result.json", out)
            raise
    finally:
        if server is not None:
            server.__exit__(None, None, None)
        if server is not None and server.log_path is not None:
            out["server_log"] = server.log_path.name
            try:
                log = server.log_path.read_text()[-4000:]
                (run_dir / "server.log").write_text(log)
            except OSError:
                pass
    out["finished_utc"] = datetime.now(tz=timezone.utc).isoformat()
    out["environment"] = environment.snapshot(ctx.engine)
    out["artifacts"] = sorted(p.name for p in run_dir.iterdir()) + ["result.json"]
    _write_json(run_dir / "result.json", out)
    out["run_dir"] = str(run_dir)
    return out


# --------------------------------------------------------------------------
# Sustained run
# --------------------------------------------------------------------------


def _run_sustained(ctx: Context, cfg: RunConfig, server) -> dict[str, Any]:
    from logforge_bench import http_mode, inprocess

    counter = {"i": 0}
    kwargs = cfg.dataset_kwargs()

    def source():
        ev = dataset.make_event(counter["i"], **kwargs)
        counter["i"] += 1
        return ev

    records: list[dict[str, Any]] = []
    samples_ref: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    stop_at = t0 + cfg.warmup_seconds + cfg.duration_s
    if cfg.mode == "http":
        res = http_mode.run(server, [], workers=cfg.workers, batch_size=cfg.batch_size, stop_at=stop_at,
                            event_source=source)
        records = res["records"]
        requests = res["requests"]
    else:
        requests = []

        def stamp(rec):
            rec["t_end"] = round(time.perf_counter() - t0, 4)

        while time.perf_counter() < stop_at:
            chunk = [source() for _ in range(max(cfg.workers * cfg.batch_size * 20, 50))]
            records.extend(inprocess.run(chunk, variant=cfg.variant, workers=cfg.workers, batch_size=cfg.batch_size,
                                         on_record=stamp)["records"])
    wall = time.perf_counter() - t0
    sustained = windows_and_verdict(records, samples_ref, cfg)
    events = [dataset.make_event(i, **kwargs) for i in range(min(counter["i"], 5000))]
    return {"records": records, "requests": requests, "wall_s": wall, "sustained": sustained,
            "dataset": {**dataset.describe(events), "note": f"characteristics of the first {len(events)} of "
                                                            f"{counter['i']} generated events (same generator)"}}


def windows_and_verdict(records: list[dict[str, Any]], _samples, cfg: RunConfig) -> dict[str, Any]:
    """Per-window throughput/latency/errors; warm-up vs steady state; the
    degradation checks. Resource samples are attached by the caller."""
    w = cfg.window_s
    buckets: dict[int, list[dict[str, Any]]] = {}
    for r in records:
        buckets.setdefault(int(r["t_end"] // w), []).append(r)
    rows = []
    for k in sorted(buckets):
        rs = buckets[k]
        lat = metrics.latency_summary([r["latency_ms"] for r in rs])
        rows.append({"window": k, "t_start_s": k * w, "t_end_s": (k + 1) * w, "events": len(rs),
                     "events_per_sec": round(len(rs) / w, 2), "p50_ms": lat["p50"], "p95_ms": lat["p95"],
                     "p99_ms": lat["p99"], "max_ms": lat["max"],
                     "failed_status": sum(1 for r in rs if r.get("status") == "FAILED"),
                     "harness_errors": sum(1 for r in rs if r.get("error")),
                     "phase": "warmup" if (k + 1) * w <= cfg.warmup_seconds else "steady"})
    # the last window is usually partial (the run stops mid-window): excluded from trend checks, reported
    if rows and rows[-1]["t_end_s"] > cfg.warmup_seconds + cfg.duration_s + 1e-9:
        rows[-1]["phase"] = "partial_final_window"
    steady = [r for r in rows if r["phase"] == "steady"]
    warm_recs = [r for r in records if r["t_end"] < cfg.warmup_seconds]
    steady_recs = [r for r in records if cfg.warmup_seconds <= r["t_end"] < cfg.warmup_seconds + cfg.duration_s]
    steady_s = cfg.duration_s
    return {"window_s": w, "warmup_s": cfg.warmup_seconds, "steady_duration_s": steady_s, "windows": rows,
            "warmup": {"events": len(warm_recs), "events_per_sec": round(len(warm_recs) / cfg.warmup_seconds, 2)
                       if cfg.warmup_seconds else None,
                       "latency": metrics.latency_summary([r["latency_ms"] for r in warm_recs])},
            "steady_state": {"events": len(steady_recs), "events_per_sec": round(len(steady_recs) / steady_s, 2),
                             "latency": metrics.latency_summary([r["latency_ms"] for r in steady_recs]),
                             "harness_errors": sum(1 for r in steady_recs if r.get("error"))},
            "degradation": degradation(steady)}


THRESHOLDS = {"throughput_drop_ratio": 0.80, "p95_growth_ratio": 1.50, "memory_growth_mb": 50.0,
              "memory_growth_ratio": 1.25}


def _slope(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def degradation(steady: list[dict[str, Any]]) -> dict[str, Any]:
    if len(steady) < 6:
        return {"verdict": "INSUFFICIENT DATA", "reason": f"only {len(steady)} steady windows (need >= 6)"}
    first, last = steady[:3], steady[-3:]
    tp0, tp1 = statistics.fmean(r["events_per_sec"] for r in first), statistics.fmean(r["events_per_sec"] for r in last)
    p0, p1 = statistics.fmean(r["p95_ms"] for r in first), statistics.fmean(r["p95_ms"] for r in last)
    errs = [r["harness_errors"] for r in steady]
    xs = [r["t_start_s"] for r in steady]
    flags = []
    tp_ratio = round(tp1 / tp0, 3) if tp0 else None
    p95_ratio = round(p1 / p0, 3) if p0 else None
    if tp_ratio is not None and tp_ratio < THRESHOLDS["throughput_drop_ratio"]:
        flags.append("THROUGHPUT_COLLAPSE")
    if p95_ratio is not None and p95_ratio > THRESHOLDS["p95_growth_ratio"]:
        flags.append("LATENCY_GROWTH")
    if sum(errs) and sum(errs[len(errs) // 2:]) > sum(errs[: len(errs) // 2]):
        flags.append("ERROR_ACCUMULATION")
    return {
        "method": "mean of the first 3 vs the last 3 steady-state windows; error counts first vs second half",
        "thresholds": THRESHOLDS,
        "throughput_first3_eps": round(tp0, 2), "throughput_last3_eps": round(tp1, 2), "throughput_ratio": tp_ratio,
        "throughput_slope_eps_per_min": round((_slope(xs, [r["events_per_sec"] for r in steady]) or 0) * 60, 3),
        "p95_first3_ms": round(p0, 3), "p95_last3_ms": round(p1, 3), "p95_ratio": p95_ratio,
        "p95_slope_ms_per_min": round((_slope(xs, [r["p95_ms"] for r in steady]) or 0) * 60, 4),
        "harness_errors_total": sum(errs),
        "flags": flags,
    }


def memory_trend(samples: list[dict[str, Any]], key: str, warmup_s: float) -> dict[str, Any]:
    pts = [(s["t"], s[key]) for s in samples if s.get(key) is not None and s["t"] >= warmup_s]
    if len(pts) < 3:
        return {"series": key, "verdict": "NOT MEASURED", "points": len(pts)}
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    growth = ys[-1] - ys[0]
    slope = _slope(xs, ys)
    flag = growth > THRESHOLDS["memory_growth_mb"] or (ys[0] and ys[-1] / ys[0] > THRESHOLDS["memory_growth_ratio"])
    return {"series": key, "points": len(pts), "start_mb": ys[0], "end_mb": ys[-1], "max_mb": max(ys),
            "growth_mb": round(growth, 2), "slope_mb_per_min": round((slope or 0) * 60, 3),
            "flag": "MEMORY_GROWTH" if flag else None}


def cpu_trend(samples: list[dict[str, Any]], key: str, warmup_s: float, window_s: float) -> list[dict[str, Any]]:
    buckets: dict[int, list[float]] = {}
    for s in samples:
        if s.get(key) is not None:
            buckets.setdefault(int(s["t"] // window_s), []).append(s[key])
    return [{"window": k, "mean_pct": round(statistics.fmean(v), 1)} for k, v in sorted(buckets.items())]


def finalize_sustained(out: dict[str, Any], cfg: RunConfig, samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach resource-over-time and the overall verdict to a sustained result."""
    s = out.get("sustained")
    if not s:
        return out
    mem = {k: memory_trend(samples, k, cfg.warmup_seconds) for k in ("server_rss_mb", "harness_rss_mb", "cgroup_mb")}
    cpu = {k: cpu_trend(samples, k, cfg.warmup_seconds, cfg.window_s) for k in ("server_cpu_pct", "harness_cpu_pct")}
    s["memory_over_time"] = mem
    s["cpu_over_time"] = cpu
    flags = list(s["degradation"].get("flags") or [])
    if cfg.target_url:
        # replicas run in other containers: their memory is judged from host-side `docker stats` per
        # container (report), never from the load generator's own RSS (it keeps every record by design)
        s["memory_note"] = "replica memory is evaluated per container from docker stats in the Step 10B report"
    else:
        mem_key = "server_rss_mb" if mem["server_rss_mb"].get("verdict") != "NOT MEASURED" else "harness_rss_mb"
        if mem[mem_key].get("flag"):
            flags.append(f"MEMORY_GROWTH ({mem_key})")
    if s["degradation"].get("verdict") == "INSUFFICIENT DATA":
        verdict = "INSUFFICIENT DATA"
    elif flags:
        verdict = "DEGRADATION DETECTED: " + ", ".join(flags)
    else:
        verdict = (f"NO DEGRADATION DETECTED within the stated thresholds over {cfg.duration_s:.0f} s of steady "
                   f"state at this load; this does not demonstrate behavior over longer runs or at larger table sizes")
    s["verdict"] = verdict
    s["all_flags"] = flags
    return out


def write_back(out: dict[str, Any]) -> None:
    p = Path(out["run_dir"]) / "result.json"
    _write_json(p, {k: v for k, v in out.items() if k != "run_dir"})


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
