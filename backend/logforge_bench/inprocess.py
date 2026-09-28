"""MODE A — IN-PROCESS benchmark: the real `ingestion_service.ingest_raw_log`
(or, for `pipeline_only`, the real deterministic pipeline without the
database) called directly, without HTTP.

Variants are FEATURE-ISOLATED EXECUTION PATHS OF THE CURRENT CODE, selected
only through existing configuration switches and the existing Phase 8
registration API. They are not reconstructions of historical releases:

  pipeline_only    detect/parse/normalize/fingerprint (app.pipeline.orchestrator), no DB
  core_nodrift     ingest_raw_log, DRIFT_ENABLED=false, RAW_VAULT_ENABLED=false, Phase 8 hooks unregistered
  v0_core          + Phase 5 structural drift (the frozen Phase 0-6 runtime path)
  v1_phase7        + Phase 7 cold raw vault write (fsync) — Phase 7 spill/raw-storage rows run in every
                   DB variant because they cannot be switched off without a code change
  v3_full          + Phase 8 persist hooks (compact lineage, revisions) = the DEFAULT configuration

Phase 8 statistical/semantic drift ("V2") and shadow validation are NOT on the
ingest path (they are on-demand jobs), so they are measured as jobs in
`jobs.py`, not as a per-event variant.

Stage timing wraps module attributes in THIS process only (a perf_counter
pair per call, ~1 µs); no production file is modified.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

VARIANTS: dict[str, dict[str, Any]] = {
    "pipeline_only": {"persist": False, "drift": False, "vault": False, "phase8": False},
    "core_nodrift": {"persist": True, "drift": False, "vault": False, "phase8": False},
    "v0_core": {"persist": True, "drift": True, "vault": False, "phase8": False},
    "v1_phase7": {"persist": True, "drift": True, "vault": True, "phase8": False},
    "v3_full": {"persist": True, "drift": True, "vault": True, "phase8": True},
    # Step 10 completion: the four named configurations (V2 = core + Phase 5 drift only).
    "V0_core": {"persist": True, "drift": False, "vault": False, "phase8": False},
    "V1_core_phase7": {"persist": True, "drift": False, "vault": True, "phase8": False},
    "V2_core_drift": {"persist": True, "drift": True, "vault": False, "phase8": False},
    "V3_full": {"persist": True, "drift": True, "vault": True, "phase8": True},
}
DEFAULT_VARIANT = "v3_full"


def apply_variant(name: str) -> dict[str, Any]:
    from app.config import get_settings
    from app.phase8 import register
    from app.services import evidence_service

    spec = VARIANTS[name]
    s = get_settings()
    s.drift_enabled = spec["drift"]
    s.raw_vault_enabled = spec["vault"]
    s.phase8_enabled = spec["phase8"]
    registered = register.register_all()  # idempotent; registers nothing when phase8_enabled is False
    return {"variant": name, **spec, "persist_hooks": [h[0] for h in evidence_service._PERSIST_HOOKS],
            "phase8_registered": registered}


class StageTimer:
    """Per-call durations of the ingestion stages, collected by wrapping the
    module attributes ingestion_service resolves at call time."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)
        self._restore: list[tuple[Any, str, Any]] = []
        self._local = threading.local()

    def _wrap(self, owner: Any, attr: str, stage: str) -> None:
        original = getattr(owner, attr)
        samples = self.samples[stage]

        def timed(*args, **kwargs):
            t = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                samples.append((time.perf_counter() - t) * 1000)

        timed.__wrapped__ = original
        setattr(owner, attr, timed)
        self._restore.append((owner, attr, original))

    def install(self) -> "StageTimer":
        from app.db.repository import event_repo
        from app.services import drift_service, evidence_service, ingestion_service, onboarding_service

        self._wrap(onboarding_service, "runtime_registry", "adapter_registry_lookup")
        self._wrap(ingestion_service, "run_pipeline", "pipeline")
        self._wrap(drift_service, "evaluate", "phase5_drift")
        self._wrap(evidence_service, "prepare_fields", "phase7_spill_prepare")
        self._wrap(evidence_service, "persist_new", "phase7_persist_total")
        self._wrap(evidence_service, "_write_overflow", "phase7_overflow_write")
        self._wrap(evidence_service, "_archive_raw", "phase7_raw_archive")
        self._wrap(evidence_service, "_run_persist_hooks", "phase8_persist_hooks_total")
        self._wrap(event_repo, "create_event", "event_insert_commit_refresh")
        hooks = evidence_service._PERSIST_HOOKS
        for i, (name, fn) in enumerate(list(hooks)):
            samples = self.samples[f"hook:{name}"]

            def timed(db, event, reprocessed, _fn=fn, _s=samples):
                t = time.perf_counter()
                try:
                    return _fn(db, event, reprocessed)
                finally:
                    _s.append((time.perf_counter() - t) * 1000)

            hooks[i] = (name, timed)
            self._restore.append((hooks, i, (name, fn)))
        return self

    def uninstall(self) -> None:
        for owner, attr, original in reversed(self._restore):
            if isinstance(owner, list):
                owner[attr] = original
            else:
                setattr(owner, attr, original)
        self._restore.clear()

    def summary(self, events: int) -> dict[str, Any]:
        from logforge_bench.metrics import latency_summary

        out = {}
        for stage, values in sorted(self.samples.items()):
            total = sum(values)
            out[stage] = {"calls": len(values), "total_ms": round(total, 3),
                          "ms_per_event": round(total / events, 4) if events else None,
                          "latency": latency_summary(values)}
        return out


def _event_record(ev, result: dict[str, Any] | None, latency_ms: float, error: str | None) -> dict[str, Any]:
    from app.pipeline.hashing import sha256_hex

    rec: dict[str, Any] = {"i": ev.index, "format": ev.format, "size_band": ev.size_band, "bytes": ev.size_bytes,
                           "malformed": ev.malformed, "malformed_kind": ev.malformed_kind,
                           "latency_ms": round(latency_ms, 4), "error": error}
    if result is not None:
        pm = result.get("processing_metadata") or {}
        drift = pm.get("drift") or {}
        spill = pm.get("extension_spill") or {}
        rec.update(
            status=result.get("status"), format_detected=result.get("format_detected"),
            adapter_id=result.get("adapter_id"), drift_status=drift.get("status"),
            original_status=drift.get("original_status"),
            extension_keys=len(result.get("extensions") or {}),
            spilled=spill.get("mode") == "SPILLED",
            raw_preserved=result.get("raw_event") == ev.raw if "raw_event" in result else None,
            sha256_verified=(result.get("raw_hash") == sha256_hex(ev.raw)) if result.get("raw_hash") else None,
        )
    return rec


def _pipeline_fields(raw: str) -> dict[str, Any]:
    from app.adapters.loader import get_adapter_registry
    from app.pipeline.orchestrator import process
    from app.services.ingestion_service import _pipeline_result_fields

    return _pipeline_result_fields(process(raw, adapter_registry=get_adapter_registry()))


def run(events: list, *, variant: str, workers: int = 1, batch_size: int = 1,
        on_record: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Ingest `events` through the variant's real code path and return
    per-event records + wall time. `batch_size` > 1 routes through
    ingestion_service.ingest_batch (which commits per item, by design)."""
    from app.db.base import SessionLocal
    from app.services import ingestion_service

    persist = VARIANTS[variant]["persist"]
    records: list[dict[str, Any]] = []
    lock = threading.Lock()

    def emit(rec):
        with lock:
            records.append(rec)
        if on_record:
            on_record(rec)

    chunks = [events[i:i + batch_size] for i in range(0, len(events), batch_size)]

    def work(chunk_iter):
        db = SessionLocal() if persist else None
        try:
            for chunk in chunk_iter:
                t = time.perf_counter()
                try:
                    if not persist:
                        results = [_pipeline_fields(ev.raw) for ev in chunk]
                        dicts = results
                    elif len(chunk) == 1:
                        dicts = [ingestion_service.ingest_raw_log(db, chunk[0].raw).model_dump(mode="json")]
                    else:
                        out = ingestion_service.ingest_batch(db, [ev.raw for ev in chunk])
                        dicts = [r.model_dump(mode="json") for r in out.results]
                    err = None
                except Exception as exc:  # noqa: BLE001 — recorded, never hidden
                    dicts, err = [None] * len(chunk), f"{type(exc).__name__}: {str(exc)[:200]}"
                    if db is not None:
                        db.rollback()
                per = (time.perf_counter() - t) * 1000 / len(chunk)
                for ev, d in zip(chunk, dicts):
                    emit(_event_record(ev, d, per, err))
        finally:
            if db is not None:
                db.close()

    t0 = time.perf_counter()
    if workers <= 1:
        work(chunks)
    else:
        it = iter(chunks)
        it_lock = threading.Lock()

        def pull():
            while True:
                with it_lock:
                    chunk = next(it, None)
                if chunk is None:
                    return
                yield chunk

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for f in [pool.submit(work, pull()) for _ in range(workers)]:
                f.result()
    wall = time.perf_counter() - t0
    records.sort(key=lambda r: r["i"])
    return {"records": records, "wall_s": wall}
