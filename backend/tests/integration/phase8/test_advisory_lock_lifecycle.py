"""F-01 regression: session-level advisory locks (scheduler tick, per-job replay slice) must be
acquired and released on the SAME PostgreSQL session, and never stay attached to an idle pooled
connection.

The original defect: the lock was taken on whatever connection the ORM session held, the session
then committed (returning that connection to the FIFO pool) and the unlock ran on a different pooled
connection, so the lock stayed on an idle connection and every other worker/replica was locked out.
The pool is pre-warmed with several idle connections so a lock/unlock pair that is not pinned to one
connection reliably lands on different connections."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.evidence.canonical import canonical_sha256
from app.services import scheduler
from app.services.phase8 import replay_service as svc
from tests.conftest import TestSessionLocal, test_engine
from tests.integration.phase8.test_replay_revisions import (  # noqa: F401
    T0, _drift_settings, act, create, drive, events, isolated_stores, job, run_slice, users,
)
from tests.integration.phase7.conftest import _phase7_defaults  # noqa: F401


def _held(key: int) -> int:
    """Sessions currently holding advisory lock `key` (bigint form: classid = high 32, objid = low 32)."""
    with test_engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
            "AND classid = :hi AND objid = :lo AND objsubid = 1"),
            {"hi": (key >> 32) & 0xFFFFFFFF, "lo": key & 0xFFFFFFFF}).scalar()


def _warm_pool(n: int = 4) -> None:
    conns = [test_engine.connect() for _ in range(n)]
    for c in conns:
        c.execute(text("SELECT 1"))
    for c in conns:
        c.close()


def _job_key(job_id: str) -> int:
    return svc._JOB_LOCK_BASE + (int(canonical_sha256(job_id)[:8], 16) % 0xFFFF)


def _other_replica_can_take(key: int) -> bool:
    """A different PostgreSQL session (another replica) can acquire the lock right now."""
    with test_engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        conn.commit()
        return bool(got)


@pytest.fixture()
def tick_session(monkeypatch):
    import app.db.base as base

    monkeypatch.setattr(base, "SessionLocal", TestSessionLocal)  # _tick imports SessionLocal lazily
    _warm_pool()


# --- scheduler tick --------------------------------------------------------------------------------------------


def test_scheduler_tick_releases_its_lock(client, tick_session, monkeypatch):
    seen = {}

    def fake_run_once(db):
        seen["held_during_tick"] = _held(scheduler._TICK_LOCK)
        db.execute(text("SELECT 1"))
        db.commit()  # the tick's work commits: the pooled connection is returned mid-tick
        return {"ok": True}

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    scheduler._tick()
    assert seen["held_during_tick"] == 1  # mutual exclusion is still in force while the tick runs
    assert _held(scheduler._TICK_LOCK) == 0
    assert _other_replica_can_take(scheduler._TICK_LOCK)


def test_scheduler_tick_releases_its_lock_when_a_step_raises(client, tick_session, monkeypatch):
    def boom(db):
        db.execute(text("SELECT 1"))
        db.commit()
        raise RuntimeError("step failed")

    monkeypatch.setattr(scheduler, "run_once", boom)
    scheduler._tick()  # logged, never raised
    assert _held(scheduler._TICK_LOCK) == 0
    assert _other_replica_can_take(scheduler._TICK_LOCK)


def test_repeated_scheduler_ticks_each_run_and_leave_no_lock(client, tick_session, monkeypatch):
    runs = []

    def fake_run_once(db):
        for _ in range(3):
            db.execute(text("SELECT 1"))
            db.commit()
        runs.append(1)
        return {"ok": True}

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    for _ in range(5):
        scheduler._tick()
        assert _held(scheduler._TICK_LOCK) == 0
    assert len(runs) == 5  # no tick was skipped because an earlier tick leaked the lock


def test_scheduler_tick_skips_while_another_replica_holds_the_lock(client, tick_session, monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "run_once", lambda db: ran.append(1) or {})
    with test_engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": scheduler._TICK_LOCK}).scalar()
        scheduler._tick()
        assert ran == []  # skipped: another session owns the tick
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": scheduler._TICK_LOCK})
        other.commit()
    scheduler._tick()
    assert ran == [1] and _held(scheduler._TICK_LOCK) == 0


def test_real_scheduler_tick_with_replay_worker_leaves_no_lock(client, events, tick_session):
    j = create(client, batch_size=3, rate_per_sec=1000)
    act(client, j, "start")
    for _ in range(10):
        scheduler._tick()
        assert _held(scheduler._TICK_LOCK) == 0 and _held(_job_key(j["id"])) == 0
        if job(client, j["id"])["status"] != "RUNNING":
            break
    assert job(client, j["id"])["status"] == "COMPLETED" and job(client, j["id"])["processed"] == 7


# --- replay slices ---------------------------------------------------------------------------------------------


def test_replay_completion_leaves_no_lock_and_keeps_progressing(client, events):
    _warm_pool()
    j = create(client, rate_per_sec=3, batch_size=3)
    act(client, j, "start")
    key = _job_key(j["id"])
    processed = [run_slice(j["id"], T0 + timedelta(seconds=60 * i))["processed"] for i in range(4)]
    assert processed == [3, 3, 1, 0]  # every slice progresses; none is refused as "locked by another worker"
    assert _held(key) == 0 and _other_replica_can_take(key)
    assert job(client, j["id"])["status"] == "COMPLETED"


def test_replay_failure_leaves_no_lock(client, events, monkeypatch):
    _warm_pool()
    j = create(client, batch_size=3)
    act(client, j, "start")

    def fail(db, job_id, now):
        db.execute(text("SELECT 1"))
        db.commit()
        raise RuntimeError("slice failed")

    monkeypatch.setattr(svc, "_slice", fail)
    with pytest.raises(RuntimeError):
        run_slice(j["id"], T0)
    assert _held(_job_key(j["id"])) == 0 and _other_replica_can_take(_job_key(j["id"]))


def test_replay_cancellation_leaves_no_lock(client, events):
    _warm_pool()
    j = create(client, batch_size=2)
    act(client, j, "start")
    assert run_slice(j["id"], T0)["processed"] == 2
    act(client, j, "cancel")
    assert run_slice(j["id"], T0 + timedelta(seconds=60))["processed"] == 0
    assert _held(_job_key(j["id"])) == 0


def test_replay_pause_resume_leaves_no_lock(client, events):
    _warm_pool()
    j = create(client, batch_size=3)
    act(client, j, "start")
    assert run_slice(j["id"], T0)["processed"] == 3
    act(client, j, "pause")
    assert run_slice(j["id"], T0 + timedelta(seconds=60))["processed"] == 0
    assert _held(_job_key(j["id"])) == 0
    act(client, j, "resume")
    assert drive(j["id"])["status"] == "COMPLETED"
    assert _held(_job_key(j["id"])) == 0


def test_lock_of_a_dead_replica_is_released_and_another_replica_resumes(client, events):
    """A replica killed mid-job: its PostgreSQL session ends, the lock goes with it, the job resumes."""
    j = create(client, batch_size=3)
    act(client, j, "start")
    key = _job_key(j["id"])
    dying = test_engine.connect()
    assert dying.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
    pid = dying.execute(text("SELECT pg_backend_pid()")).scalar()
    dying.commit()
    assert run_slice(j["id"], T0)["skipped"] == "locked by another worker"
    with test_engine.connect() as admin:
        assert admin.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid}).scalar()
        admin.commit()
    dying.invalidate()
    dying.close()
    for _ in range(50):
        if _held(key) == 0:
            break
    assert run_slice(j["id"], T0 + timedelta(seconds=1))["processed"] == 3
    assert drive(j["id"])["status"] == "COMPLETED" and _held(key) == 0
