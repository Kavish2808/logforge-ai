"""Lightweight in-process background scheduler (no Temporal, no broker).

One daemon thread runs `run_once` every SCHEDULER_INTERVAL_SECONDS:
    raw vault backfill/retry -> Merkle sealing -> review SLA sweep -> alert sweep.

Multi-process safe: each tick takes a Postgres session-level advisory lock
(pg_try_advisory_lock), so with several API workers only one runs a tick at
a time; the others skip it. Every step is idempotent, and a failing step is
logged and never stops the other steps or the next tick.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)
_TICK_LOCK = 0x4C46_5449_434B  # "LFTICK"

_state: dict[str, Any] = {"thread": None, "stop": None, "last_run": None, "last_result": None}

# Phase 8 hook (additive): extra steps run after the four Phase 7 steps, in
# registration order, under the same per-step isolation.
_EXTRA_STEPS: list[tuple[str, Any]] = []


def register_step(name: str, fn) -> None:
    """Register an extra scheduler step `fn(db) -> dict` (idempotent per name)."""
    _EXTRA_STEPS[:] = [s for s in _EXTRA_STEPS if s[0] != name] + [(name, fn)]


def unregister_step(name: str) -> None:
    _EXTRA_STEPS[:] = [s for s in _EXTRA_STEPS if s[0] != name]


def run_once(db: Session) -> dict[str, Any]:
    from app.services import evidence_service, monitor_service, sla_service

    result: dict[str, Any] = {"started_at": datetime.now(tz=timezone.utc).isoformat()}
    steps = (
        ("raw_vault_backfill", lambda: evidence_service.backfill_vault(db)),
        ("merkle_seal", lambda: {"batches_sealed": len(evidence_service.seal(db))}),
        ("review_sla", lambda: sla_service.sweep(db)),
        ("alerts", lambda: monitor_service.sweep(db)),
    ) + tuple((name, (lambda fn=fn: fn(db))) for name, fn in _EXTRA_STEPS)
    for name, step in steps:
        try:
            result[name] = step()
        except Exception as exc:  # noqa: BLE001 — one failing step never blocks the others
            db.rollback()
            logger.exception("Scheduler step %s failed.", name)
            result[name] = {"error": type(exc).__name__}
    result["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    return result


def _tick() -> None:
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        got = db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _TICK_LOCK}).scalar()
        db.commit()
        if not got:
            return
        try:
            _state["last_result"] = run_once(db)
            _state["last_run"] = datetime.now(tz=timezone.utc).isoformat()
        finally:
            db.rollback()
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _TICK_LOCK})
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduler tick failed.")
    finally:
        db.close()


def _loop(stop: threading.Event, interval: int) -> None:
    while not stop.wait(interval):
        _tick()


def start() -> bool:
    settings = get_settings()
    if not settings.scheduler_enabled or _state["thread"] is not None:
        return False
    stop = threading.Event()
    thread = threading.Thread(target=_loop, args=(stop, settings.scheduler_interval_seconds),
                              name="logforge-phase7-scheduler", daemon=True)
    _state.update(thread=thread, stop=stop)
    thread.start()
    logger.info("Phase 7 scheduler started (every %ss).", settings.scheduler_interval_seconds)
    return True


def stop() -> None:
    if _state["stop"] is not None:
        _state["stop"].set()
    _state.update(thread=None, stop=None)


def status() -> dict[str, Any]:
    settings = get_settings()
    return {"enabled": settings.scheduler_enabled, "running": _state["thread"] is not None,
            "interval_seconds": settings.scheduler_interval_seconds,
            "last_run": _state["last_run"], "last_result": _state["last_result"]}
