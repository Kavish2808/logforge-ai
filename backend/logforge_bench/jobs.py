"""Off-ingest-path work, measured as jobs over the events a preceding
in-process run stored in the benchmark database:

- phase8_statistical_drift  advanced_drift_service.analyze(persist=False) — "V2"
- shadow_validation         shadow_service.compare() for a shipped adapter against
                            an IDENTICAL candidate (the real stratified comparison,
                            3 timed pipeline repeats per side per event; read-only)
- merkle_seal               evidence_service.seal(force=True) — Phase 7 scheduler work
- compact_lineage_storage   compact_lineage_service.benchmark() — the existing
                            Phase 8 storage measurement (pg_column_size based)

Each job runs the real service function; nothing is re-implemented here.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any


def _timed(fn):
    t = time.perf_counter()
    try:
        return fn(), round((time.perf_counter() - t) * 1000, 2), None
    except Exception as exc:  # noqa: BLE001 — a failed job is recorded, not hidden
        return None, round((time.perf_counter() - t) * 1000, 2), f"{type(exc).__name__}: {str(exc)[:300]}"


def phase8_drift(db, *, t_first: datetime, t_last: datetime, events: int) -> dict[str, Any]:
    from app.services.phase8 import advanced_drift_service as ads

    # Windows are whole hours; ending the 1 h current window one hour after the
    # run's midpoint makes the first half of the run the baseline window and
    # the second half the current window.
    mid = t_first + (t_last - t_first) / 2
    end = mid + timedelta(hours=1)
    result, ms, err = _timed(lambda: ads.analyze(db, window_end=end, baseline_hours=168, current_hours=1, persist=False))
    db.rollback()
    out: dict[str, Any] = {"job": "phase8_statistical_drift", "duration_ms": ms, "error": err,
                           "events_in_db": events, "window_end": end.isoformat(), "persist": False}
    if result:
        analyzed = [s for s in result["sources"] if s.get("status") == "ANALYZED"]
        rows = sum(s.get("baseline_n", 0) + s.get("current_n", 0) for s in result["sources"])
        out.update(sources=len(result["sources"]), sources_analyzed=len(analyzed),
                   sources_skipped={s["source_key"]: s.get("reason") for s in result["sources"] if s.get("status") != "ANALYZED"},
                   rows_read=rows, findings=result["findings"], advisories=result["advisories"],
                   ms_per_row_read=round(ms / rows, 4) if rows else None)
    return out


def shadow(db, *, source: str) -> dict[str, Any]:
    from app.services import onboarding_service
    from app.services.phase8 import shadow_service

    registry = onboarding_service.runtime_registry(db)
    candidate = registry.get(source)
    if candidate is None:
        return {"job": "shadow_validation", "source": source, "error": f"no adapter '{source}' in the registry"}
    result, ms, err = _timed(lambda: shadow_service.compare(db, source, candidate))
    db.rollback()
    out: dict[str, Any] = {"job": "shadow_validation", "source": source, "candidate": "identical to current adapter",
                           "duration_ms": ms, "error": err}
    if result:
        verdict, tripped, reasons = shadow_service.verdict(result)
        n = result["sample_count"]
        out.update(sample_count=n, ms_per_sampled_event=round(ms / n, 3) if n else None,
                   pipeline_runs=n * 2 * shadow_service.LATENCY_REPEATS, verdict=verdict, breaker_tripped=tripped,
                   reasons=[r["code"] for r in reasons], per_event_pipeline_latency_ms=result["latency"],
                   strata={k: len(v["selected"]) for k, v in result["strata"].items()})
    return out


def merkle_seal(db) -> dict[str, Any]:
    from app.services import evidence_service

    batches, ms, err = _timed(lambda: evidence_service.seal(db, force=True, max_batches=1000))
    sealed = sum(b.event_count for b in batches or [])
    return {"job": "merkle_seal", "duration_ms": ms, "error": err, "batches": len(batches or []),
            "events_sealed": sealed, "ms_per_event": round(ms / sealed, 4) if sealed else None}


def lineage_storage(db, sample: int = 200) -> dict[str, Any]:
    from app.services.phase8 import compact_lineage_service

    result, ms, err = _timed(lambda: compact_lineage_service.benchmark(db, sample=sample))
    db.rollback()
    return {"job": "compact_lineage_storage", "duration_ms": ms, "error": err, "result": result}


def run_all(db, *, t_first: datetime, t_last: datetime, events: int,
            shadow_sources: tuple[str, ...] = ("fortinet", "paloalto_cef")) -> list[dict[str, Any]]:
    jobs = [phase8_drift(db, t_first=t_first, t_last=t_last, events=events)]
    jobs += [shadow(db, source=s) for s in shadow_sources]
    jobs.append(lineage_storage(db))
    jobs.append(merkle_seal(db))  # last: it writes (batch rows + anchors)
    return jobs


def received_range(db) -> tuple[datetime, datetime]:
    from sqlalchemy import text

    lo, hi = db.execute(text("SELECT min(received_at), max(received_at) FROM events")).one()
    now = datetime.now(tz=timezone.utc)
    return lo or now, hi or now
