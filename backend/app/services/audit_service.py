"""Tamper-evident, hash-chained audit log.

Every record's current_hash is SHA-256 over the canonical JSON of all its
content (seq, audit_id, actor, role, authenticated, action, object, decision,
timestamp, details, evidence_ref) *and* the previous record's hash. Editing,
deleting or reordering any record breaks verification from that point on.

Appends are serialized with a transaction-scoped Postgres advisory lock so
concurrent writers can never fork the chain. Records are only ever inserted;
nothing in the application updates or deletes them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.ids import generate_event_id
from app.db.models.governance import AuditLog
from app.evidence.canonical import canonical_sha256
from app.evidence.merkle import GENESIS

_AUDIT_LOCK = 0x4C46_4155_4449_54  # "LFAUDIT"

SUCCESS = "SUCCESS"
DENIED = "DENIED"
FAILED = "FAILED"


def _content(row: AuditLog) -> dict[str, Any]:
    ts = row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc)
    return {
        "seq": row.seq,
        "audit_id": row.audit_id,
        "actor": row.actor,
        "role": row.role,
        "authenticated": row.authenticated,
        "action": row.action,
        "object_type": row.object_type,
        "object_id": row.object_id,
        "decision": row.decision,
        "timestamp": ts.astimezone(timezone.utc).isoformat(),
        "details": row.details or {},
        "evidence_ref": row.evidence_ref,
        "previous_hash": row.previous_hash,
    }


def compute_hash(row: AuditLog) -> str:
    return canonical_sha256(_content(row))


def record(
    db: Session,
    *,
    actor: str,
    role: str | None,
    authenticated: bool,
    action: str,
    object_type: str,
    object_id: str | None,
    decision: str = SUCCESS,
    details: dict[str, Any] | None = None,
    evidence_ref: str | None = None,
    commit: bool = True,
) -> AuditLog:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK})
    last = db.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)).scalars().first()
    row = AuditLog(
        seq=(last.seq + 1) if last else 1,
        audit_id=generate_event_id(),
        actor=actor[:128],
        role=role,
        authenticated=authenticated,
        action=action[:64],
        object_type=object_type[:64],
        object_id=(object_id or None) and str(object_id)[:128],
        decision=decision,
        # Microsecond precision survives the Postgres round trip unchanged.
        timestamp=datetime.now(tz=timezone.utc),
        details=_jsonable(details or {}),
        evidence_ref=evidence_ref,
        previous_hash=last.current_hash if last else GENESIS,
        current_hash="",
    )
    row.current_hash = compute_hash(row)
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def verify(db: Session, *, batch_size: int = 1000, recent: int | None = None) -> dict[str, Any]:
    """Walk the chain in order (all of it, or only the `recent` newest records
    anchored on their predecessor's stored hash). Reports the first break and
    every record whose own hash no longer matches its content."""
    expected_prev = GENESIS
    expected_seq = 1
    checked = 0
    problems: list[dict[str, Any]] = []
    last_seq = 0
    if recent:
        head = db.execute(select(func.max(AuditLog.seq))).scalar() or 0
        start = max(1, head - recent + 1)
        if start > 1:
            before = db.get(AuditLog, start - 1)
            expected_prev = before.current_hash if before else GENESIS
            expected_seq, last_seq = start, start - 1
    while True:
        rows = db.execute(
            select(AuditLog).where(AuditLog.seq > last_seq).order_by(AuditLog.seq).limit(batch_size)
        ).scalars().all()
        if not rows:
            break
        for row in rows:
            checked += 1
            if row.seq != expected_seq:
                problems.append({"seq": row.seq, "problem": "SEQUENCE_GAP", "expected_seq": expected_seq})
            if row.previous_hash != expected_prev:
                problems.append({"seq": row.seq, "problem": "BROKEN_LINK",
                                 "detail": "previous_hash does not match the preceding record's hash"})
            recomputed = compute_hash(row)
            if recomputed != row.current_hash:
                problems.append({"seq": row.seq, "problem": "CONTENT_MODIFIED",
                                 "detail": "record content no longer matches its hash"})
            expected_prev = row.current_hash
            expected_seq = row.seq + 1
            last_seq = row.seq
        if len(rows) < batch_size:
            break
    return {
        "valid": not problems,
        "records_checked": checked,
        "head_hash": expected_prev if checked else None,
        "head_seq": last_seq or None,
        "first_break_seq": problems[0]["seq"] if problems else None,
        "problems": problems[:100],
        "scope": f"newest {recent} records" if recent else "full chain",
        "verified_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def list_records(db: Session, *, limit: int, before_seq: int | None = None, action: str | None = None,
                 object_type: str | None = None, object_id: str | None = None, actor: str | None = None) -> list[AuditLog]:
    stmt = select(AuditLog)
    if before_seq:
        stmt = stmt.where(AuditLog.seq < before_seq)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    return list(db.execute(stmt.order_by(AuditLog.seq.desc()).limit(limit)).scalars().all())


def to_dict(row: AuditLog) -> dict[str, Any]:
    return {**_content(row), "current_hash": row.current_hash}


def stats(db: Session) -> dict[str, Any]:
    total = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    by_decision = dict(db.execute(select(AuditLog.decision, func.count()).group_by(AuditLog.decision)).all())
    head = db.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)).scalars().first()
    return {"total": total, "by_decision": by_decision,
            "head": None if head is None else {"seq": head.seq, "hash": head.current_hash, "at": head.timestamp}}


def makers(db: Session, object_type: str, object_id: str, actions: tuple[str, ...]) -> set[str]:
    """Authenticated actors who successfully performed a proposing action on an object."""
    rows = db.execute(
        select(AuditLog.actor).where(
            AuditLog.object_type == object_type, AuditLog.object_id == object_id,
            AuditLog.action.in_(actions), AuditLog.decision == SUCCESS, AuditLog.authenticated.is_(True),
        )
    ).scalars().all()
    return set(rows)
