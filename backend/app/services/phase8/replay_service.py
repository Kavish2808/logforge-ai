"""Phase 8 Step 7: rate-limited replay jobs and revision-aware rollback.

Replay re-runs stored events through the EXISTING reprocess path
(ingestion_service.reprocess_event -> same pipeline, drift evaluation,
Phase 7 evidence hook) and records one revision per event per job. There is
no second parsing engine.

Job selection is fixed at creation: events of `adapter_id` (optionally only
those processed with `from_version`), received in [window_start,
window_end), and never after the job was created. Processing walks that set
with a keyset cursor (received_at, event_id), so it is deterministic and
resumable; the cursor, counters and status are committed after every event.

Rate limiting is a token bucket evaluated per slice: a slice may process at
most min(batch_size, rate_per_sec x seconds since the previous slice) events
(the first slice gets min(batch_size, ceil(rate))). The scheduler step runs
one slice per RUNNING job per tick, so work per tick is bounded; pause and
cancel are re-read before every event (backpressure / cooperative stop).

Integrity per event: SHA-256 of the raw bytes before == after == stored
raw_hash; any mismatch fails the job immediately. A slice ends with a Merkle
verification of the newest batches.

Governance: jobs over LARGE_REPLAY_THRESHOLD events need an authenticated
SECURITY_ENGINEER/SOC_ADMIN creator with a reason, start in PENDING_APPROVAL
and must be started by a *different* authorized actor (maker-checker).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.db.models.onboarding import ADAPTER_ACTIVE, ADAPTER_SUPERSEDED, OnboardedAdapter
from app.db.models.phase8 import (
    REPLAY_CANCELLED,
    REPLAY_COMPLETED,
    REPLAY_FAILED,
    REPLAY_PAUSED,
    REPLAY_PENDING,
    REPLAY_PENDING_APPROVAL,
    REPLAY_RUNNING,
    EventRevision,
    ReplayJob,
)
from app.evidence.canonical import canonical_sha256
from app.pipeline.hashing import sha256_hex
from app.services import evidence_service, ingestion_service, onboarding_service
from app.services.phase8 import compact_lineage_service, revision_service

logger = logging.getLogger(__name__)

LARGE_REPLAY_THRESHOLD = 10_000
DEFAULT_RATE_PER_SEC = 50.0
MAX_RATE_PER_SEC = 1000.0
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1000
MAX_ERRORS_KEPT = 20
MERKLE_RECENT_BATCHES = 5
STEP_NAME = "replay_worker"
TRIGGER_MANUAL, TRIGGER_ROLLBACK = "MANUAL", "ROLLBACK"
ANY_VERSION = "*"
_JOB_LOCK_BASE = 0x4C46_5250_0000  # "LFRP" + per-job offset
TERMINAL = (REPLAY_COMPLETED, REPLAY_CANCELLED, REPLAY_FAILED)


class ReplayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def active_version(db: Session, adapter_id: str) -> str | None:
    row = db.execute(select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == adapter_id,
                                                    OnboardedAdapter.status == ADAPTER_ACTIVE)).scalars().first()
    if row is not None:
        return str(row.version)
    shipped = onboarding_service.runtime_registry(db).get(adapter_id)
    return shipped.version if shipped is not None else None


# --------------------------------------------------------------------------
# Selection (fixed at creation, keyset traversal)
# --------------------------------------------------------------------------


def _selection(job: ReplayJob):
    cutoff = datetime.fromisoformat(job.cursor["selection_cutoff"])
    conds = [Event.adapter_id == job.adapter_id, Event.received_at <= cutoff]
    if job.from_version != ANY_VERSION:
        conds.append(Event.adapter_version == job.from_version)
    if job.window_start is not None:
        conds.append(Event.received_at >= job.window_start)
    if job.window_end is not None:
        conds.append(Event.received_at < job.window_end)
    return and_(*conds)


def count_selection(db: Session, *, adapter_id: str, from_version: str, window_start: datetime | None,
                    window_end: datetime | None, cutoff: datetime) -> int:
    probe = ReplayJob(adapter_id=adapter_id, from_version=from_version, window_start=window_start,
                      window_end=window_end, cursor={"selection_cutoff": cutoff.isoformat()})
    return db.execute(select(func.count()).select_from(Event).where(_selection(probe))).scalar_one()


def _next_events(db: Session, job: ReplayJob, limit: int) -> list[Event]:
    stmt = select(Event).where(_selection(job))
    after = (job.cursor or {}).get("after")
    if after:
        at = datetime.fromisoformat(after["received_at"])
        stmt = stmt.where(or_(Event.received_at > at, and_(Event.received_at == at, Event.event_id > after["event_id"])))
    return list(db.execute(stmt.order_by(Event.received_at, Event.event_id).limit(limit)).scalars().all())


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def create(db: Session, *, adapter_id: str, reason: str, actor, from_version: str | None = None,
           window_start: datetime | None = None, window_end: datetime | None = None,
           rate_per_sec: float = DEFAULT_RATE_PER_SEC, batch_size: int = DEFAULT_BATCH_SIZE,
           trigger: str = TRIGGER_MANUAL, to_version: str | None = None) -> ReplayJob:
    if not reason.strip():
        raise ReplayError(422, "A replay needs an explicit reason.")
    if not (0 < rate_per_sec <= MAX_RATE_PER_SEC) or not (1 <= batch_size <= MAX_BATCH_SIZE):
        raise ReplayError(422, f"rate_per_sec must be in (0, {MAX_RATE_PER_SEC}] and batch_size in [1, {MAX_BATCH_SIZE}].")
    target = to_version or active_version(db, adapter_id)
    if target is None:
        raise ReplayError(409, f"Adapter '{adapter_id}' is not available in the runtime registry; nothing to replay against.")
    now = _now()
    from_version = from_version or ANY_VERSION
    total = count_selection(db, adapter_id=adapter_id, from_version=from_version, window_start=window_start,
                            window_end=window_end, cutoff=now)
    large = total > LARGE_REPLAY_THRESHOLD
    if large and not (actor.authenticated and actor.role in ("SECURITY_ENGINEER", "SOC_ADMIN")):
        raise ReplayError(403, f"Replaying {total} events (> {LARGE_REPLAY_THRESHOLD}) requires an authenticated "
                               "SECURITY_ENGINEER or SOC_ADMIN.")
    job = ReplayJob(
        id=generate_event_id(), trigger=trigger, adapter_id=adapter_id, from_version=from_version, to_version=target,
        window_start=window_start, window_end=window_end,
        status=REPLAY_PENDING_APPROVAL if large else REPLAY_PENDING, total=total, processed=0, succeeded=0, failed=0,
        skipped=0, rate_per_sec=rate_per_sec, batch_size=batch_size, reason=reason.strip(),
        created_by=actor.username, approved_by=None, error=None,
        cursor={"selection_cutoff": now.isoformat(), "after": None, "created_at": now.isoformat(),
                "started_at": None, "completed_at": None, "last_slice_at": None, "large": large,
                "raw_hash_mismatches": 0, "revisions_written": 0, "errors": [], "merkle": None, "slices": 0},
    )
    db.add(job)
    db.flush()
    return job


def _set_cursor(job: ReplayJob, **values) -> None:
    job.cursor = {**(job.cursor or {}), **values}  # reassign so the JSONB change is detected


def start(db: Session, job: ReplayJob, *, actor) -> ReplayJob:
    if job.status == REPLAY_PENDING_APPROVAL:
        if not (actor.authenticated and actor.role in ("SECURITY_ENGINEER", "SOC_ADMIN")):
            raise ReplayError(403, "This large replay must be started by an authenticated SECURITY_ENGINEER or SOC_ADMIN.")
        if actor.username == job.created_by:
            raise ReplayError(403, f"Maker-checker: '{actor.username}' created this large replay and cannot also start it.")
        job.approved_by = actor.username
    elif job.status != REPLAY_PENDING:
        raise ReplayError(409, f"Replay job is {job.status}; only PENDING / PENDING_APPROVAL jobs can be started.")
    job.status = REPLAY_RUNNING
    _set_cursor(job, started_at=_now().isoformat())
    return job


def pause(job: ReplayJob) -> ReplayJob:
    if job.status != REPLAY_RUNNING:
        raise ReplayError(409, f"Replay job is {job.status}; only RUNNING jobs can be paused.")
    job.status = REPLAY_PAUSED
    return job


def resume(job: ReplayJob) -> ReplayJob:
    if job.status != REPLAY_PAUSED:
        raise ReplayError(409, f"Replay job is {job.status}; only PAUSED jobs can be resumed.")
    job.status = REPLAY_RUNNING
    return job


def cancel(job: ReplayJob) -> ReplayJob:
    if job.status in TERMINAL:
        raise ReplayError(409, f"Replay job is already {job.status}.")
    job.status = REPLAY_CANCELLED
    _set_cursor(job, completed_at=_now().isoformat())
    return job


def get(db: Session, job_id: str) -> ReplayJob:
    job = db.get(ReplayJob, job_id)
    if job is None:
        raise ReplayError(404, f"Replay job '{job_id}' not found")
    return job


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def budget(job: ReplayJob, now: datetime) -> int:
    last = (job.cursor or {}).get("last_slice_at")
    if last is None:
        return max(1, min(job.batch_size, math.ceil(job.rate_per_sec)))
    elapsed = max(0.0, (now - datetime.fromisoformat(last)).total_seconds())
    return min(job.batch_size, int(job.rate_per_sec * elapsed))


def _status(db: Session, job_id: str) -> str:
    return db.execute(select(ReplayJob.status).where(ReplayJob.id == job_id)).scalar_one()


def _fail(db: Session, job: ReplayJob, message: str) -> None:
    job.status = REPLAY_FAILED
    job.error = message[:1000]
    _set_cursor(job, completed_at=_now().isoformat())
    db.commit()


def run_slice(db: Session, job_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Process at most one rate-limited slice of a RUNNING job. Safe to call
    concurrently (per-job advisory lock) and after any interruption."""
    lock = _JOB_LOCK_BASE + (int(canonical_sha256(job_id)[:8], 16) % 0xFFFF)
    if not db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock}).scalar():
        db.rollback()
        return {"job_id": job_id, "skipped": "locked by another worker"}
    try:
        return _slice(db, job_id, now or _now())
    finally:
        db.rollback()
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock})
        db.commit()


def _slice(db: Session, job_id: str, now: datetime) -> dict[str, Any]:
    job = get(db, job_id)
    if job.status != REPLAY_RUNNING:
        return {"job_id": job_id, "status": job.status, "processed": 0}
    if active_version(db, job.adapter_id) != job.to_version:
        _fail(db, job, f"Target {job.adapter_id} v{job.to_version} is no longer the active version "
                       f"(now v{active_version(db, job.adapter_id)}); refusing to replay against an unexpected revision.")
        return {"job_id": job_id, "status": job.status, "processed": 0}
    allowed = budget(job, now)
    _set_cursor(job, last_slice_at=now.isoformat(), slices=(job.cursor or {}).get("slices", 0) + 1)
    db.commit()
    done = 0
    events = _next_events(db, job, allowed) if allowed else []
    for event in events:
        if _status(db, job_id) != REPLAY_RUNNING:  # pause / cancel take effect between events
            break
        outcome = _replay_one(db, job, event)
        job = get(db, job_id)
        job.processed += 1
        job.succeeded += outcome == "ok"
        job.failed += outcome == "failed"
        job.skipped += outcome == "skipped"
        _set_cursor(job, after={"received_at": event.received_at.isoformat(), "event_id": event.event_id})
        db.commit()
        done += 1
        if outcome == "raw_mismatch":
            _fail(db, job, f"Raw SHA-256 changed for event {event.event_id}; replay stopped.")
            return {"job_id": job_id, "status": job.status, "processed": done}
    job = get(db, job_id)
    if job.status == REPLAY_RUNNING and allowed and len(events) < allowed:
        verification = evidence_service.verify_chain(db, recent=MERKLE_RECENT_BATCHES)
        _set_cursor(job, completed_at=_now().isoformat(),
                    merkle={"valid": verification["valid"], "scope": verification["scope"],
                            "problems": verification["problems"][:10]})
        job.status = REPLAY_COMPLETED if verification["valid"] else REPLAY_FAILED
        if not verification["valid"]:
            job.error = "Merkle verification failed after replay; see cursor.merkle."
        db.commit()
    return {"job_id": job_id, "status": job.status, "processed": done, "budget": allowed}


def _replay_one(db: Session, job: ReplayJob, event: Event) -> str:
    event_id = event.event_id
    if db.execute(select(EventRevision.id).where(EventRevision.event_id == event_id,
                                                 EventRevision.replay_job_id == job.id)).first():
        return "skipped"  # already replayed by this job (idempotent resume)
    before = sha256_hex(event.raw_event)
    raw_hash = event.raw_hash
    try:
        revision_service.ensure_original(db, event)
        with revision_service.context(db, trigger=revision_service.REPLAY, actor=job.approved_by or job.created_by,
                                      reason=f"replay job {job.id} ({job.trigger}): {job.reason}", replay_job_id=job.id):
            ingestion_service.reprocess_event(db, event)
        bits = ["REPLAY"] + (["ROLLBACK"] if job.trigger == TRIGGER_ROLLBACK else [])
        for bit in bits:
            compact_lineage_service.mark_exception(db, [event_id], bit)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — one event never stops the job; the error is recorded
        db.rollback()
        logger.exception("Replay of event %s in job %s failed.", event_id, job.id)
        j = get(db, job.id)
        _set_cursor(j, errors=((j.cursor or {}).get("errors") or [])[-(MAX_ERRORS_KEPT - 1):]
                    + [{"event_id": event_id, "error": type(exc).__name__}])
        db.commit()
        return "failed"
    stored = db.get(Event, event_id)
    after = sha256_hex(stored.raw_event)
    if not (before == after == raw_hash == stored.raw_hash):
        j = get(db, job.id)
        _set_cursor(j, raw_hash_mismatches=(j.cursor or {}).get("raw_hash_mismatches", 0) + 1)
        db.commit()
        return "raw_mismatch"
    written = db.execute(select(EventRevision.id).where(EventRevision.event_id == event_id,
                                                        EventRevision.replay_job_id == job.id)).first()
    j = get(db, job.id)
    if written is None:
        _set_cursor(j, errors=((j.cursor or {}).get("errors") or [])[-(MAX_ERRORS_KEPT - 1):]
                    + [{"event_id": event_id, "error": "REVISION_NOT_RECORDED"}])
        db.commit()
        return "failed"
    _set_cursor(j, revisions_written=(j.cursor or {}).get("revisions_written", 0) + 1)
    db.commit()
    return "ok"


def worker_step(db: Session) -> dict[str, Any]:
    """Scheduler step: one slice per RUNNING job (oldest first, at most 5 jobs per tick)."""
    ids = db.execute(select(ReplayJob.id).where(ReplayJob.status == REPLAY_RUNNING)
                     .order_by(ReplayJob.created_at, ReplayJob.id).limit(5)).scalars().all()
    db.commit()
    return {"jobs": [run_slice(db, i) for i in ids]}


def drive(db: Session, job_id: str, *, max_slices: int = 1000, sleep=None) -> ReplayJob:
    """Run a job to a stop state honoring its rate (tests / operators). Bounded by max_slices."""
    import time

    sleep = sleep or time.sleep
    for _ in range(max_slices):
        result = run_slice(db, job_id)
        if result.get("status") != REPLAY_RUNNING:
            break
        job = get(db, job_id)
        sleep(job.batch_size / job.rate_per_sec)  # time for the bucket to refill one full batch
    return get(db, job_id)


def list_jobs(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(select(ReplayJob).order_by(ReplayJob.created_at.desc(), ReplayJob.id.desc()).limit(limit)).scalars()
    return [job_dict(j) for j in rows]


def job_dict(job: ReplayJob) -> dict[str, Any]:
    c = job.cursor or {}
    return {"id": job.id, "trigger": job.trigger, "source": job.adapter_id, "adapter_id": job.adapter_id,
            "selection": {"from_version": job.from_version, "window_start": job.window_start,
                          "window_end": job.window_end, "selection_cutoff": c.get("selection_cutoff")},
            "target_version": job.to_version, "status": job.status, "total": job.total, "processed": job.processed,
            "succeeded": job.succeeded, "failed": job.failed, "skipped": job.skipped,
            "rate_per_sec": job.rate_per_sec, "batch_size": job.batch_size, "reason": job.reason,
            "created_by": job.created_by, "approved_by": job.approved_by, "error": job.error,
            "created_at": job.created_at, "started_at": c.get("started_at"), "completed_at": c.get("completed_at"),
            "updated_at": job.updated_at, "checkpoint": c.get("after"), "large_replay": c.get("large"),
            "integrity": {"raw_hash_mismatches": c.get("raw_hash_mismatches", 0),
                          "revisions_written": c.get("revisions_written", 0), "merkle": c.get("merkle")},
            "errors": c.get("errors", []), "slices": c.get("slices", 0)}


# --------------------------------------------------------------------------
# Revision-aware rollback
# --------------------------------------------------------------------------


def rollback(db: Session, adapter_id: str, *, reason: str, actor, replay: bool,
             rate_per_sec: float = DEFAULT_RATE_PER_SEC, batch_size: int = DEFAULT_BATCH_SIZE) -> dict[str, Any]:
    """Restore the previous approved version of an onboarded adapter through
    the existing Phase 3 / Phase 6 rollback, bracketed by snapshots and
    integrity checks; optionally queue a ROLLBACK replay of the events the
    withdrawn version processed (never started automatically)."""
    from app.services import learning_service

    if not reason.strip():
        raise ReplayError(422, "A rollback needs an explicit reason.")
    versions = list(db.execute(select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == adapter_id)
                               .order_by(OnboardedAdapter.version)).scalars().all())
    active = next((v for v in versions if v.status == ADAPTER_ACTIVE), None)
    if active is None:
        raise ReplayError(409, f"Adapter '{adapter_id}' has no active onboarded version to roll back.")
    target = max((v for v in versions if v.status == ADAPTER_SUPERSEDED and v.version < active.version),
                 key=lambda v: v.version, default=None)
    if target is None:
        raise ReplayError(409, f"Adapter '{adapter_id}' v{active.version} has no previous approved version to restore.")
    per_version = dict(db.execute(select(Event.adapter_version, func.count()).where(Event.adapter_id == adapter_id)
                                  .group_by(Event.adapter_version)).all())
    before = {"active_version": active.version, "active_mapping_sha256": canonical_sha256(active.mapping),
              "target_version": target.version, "target_mapping_sha256": canonical_sha256(target.mapping),
              "events_per_version": {str(k): v for k, v in per_version.items()},
              "merkle": _merkle(db), "at": _now().isoformat()}
    session_id = (active.validation_summary or {}).get("learning_session_id")
    session = db.get(LearningSession, session_id) if session_id else None
    if session is not None and session.status == "ACTIVE" and session.target_adapter_row_id == active.id:
        learning_service.rollback(db, session.id, reason=reason, requested_by=actor.username)  # Phase 6 semantics
        path = f"phase6 learning rollback of session {session.id}"
    else:
        onboarding_service.rollback(db, adapter_id, reason=reason, requested_by=actor.username)  # Phase 3 semantics
        path = "phase3 adapter rollback"
    restored = active_version(db, adapter_id)
    if restored != str(target.version):
        raise ReplayError(500, f"Rollback did not restore v{target.version} (active is v{restored}).")
    after = {"active_version": restored, "merkle": _merkle(db), "path": path, "at": _now().isoformat()}
    job = None
    if replay:
        job = create(db, adapter_id=adapter_id, reason=f"rollback v{active.version} -> v{target.version}: {reason}",
                     actor=actor, from_version=str(active.version), rate_per_sec=rate_per_sec, batch_size=batch_size,
                     trigger=TRIGGER_ROLLBACK, to_version=str(target.version))
    db.commit()
    return {"adapter_id": adapter_id, "rolled_back_version": active.version, "restored_version": target.version,
            "before": before, "after": after, "replay_job": job_dict(job) if job else None,
            "note": "Events are not modified by the rollback itself; the replay job (if any) records new revisions "
                    "and must be started explicitly."}


def _merkle(db: Session) -> dict[str, Any]:
    v = evidence_service.verify_chain(db, recent=MERKLE_RECENT_BATCHES)
    return {"valid": v["valid"], "batches_checked": v["batches_checked"], "head": v["head"], "scope": v["scope"]}
