"""Session-level PostgreSQL advisory locks pinned to one dedicated connection.

A session-level advisory lock belongs to the PostgreSQL session (connection) that took it. An ORM
Session hands its connection back to the pool on every commit/rollback, so taking the lock through
the ORM session and releasing it later through the same ORM session can run the unlock on a
different pooled connection -- leaving the lock attached to an idle pooled connection and locking
every other worker out (F-01). Here the lock is taken, held and released on one connection that
stays checked out for the whole critical section. The protected work itself keeps using the
caller's ORM session and may commit freely.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@contextmanager
def try_advisory_lock(db: Session, key: int) -> Iterator[bool]:
    """Yield True if `key` was acquired (released on exit, even on error), False if another session holds it."""
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    conn = engine.connect()
    got = False
    try:
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        conn.commit()  # end the transaction; the session-level lock stays on this checked-out connection
        yield got
    finally:
        try:
            if got:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
        except Exception:  # noqa: BLE001 — never hand a connection that may still hold the lock back to the pool
            logger.warning("Advisory unlock of %s failed; discarding the connection.", key)
            conn.invalidate()
        finally:
            conn.close()
