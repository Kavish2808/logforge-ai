"""Adaptive UNDER_REVIEW SLA.

Every item awaiting a human decision gets a durable deadline:

    DRIFT_EVENT         events in UNDER_REVIEW   (severity = drift severity)
    ONBOARDING_SESSION  validated proposals awaiting approval
    LEARNING_SESSION    open learning sessions   (severity = learning risk)

`sweep` (run by the background scheduler, and on demand) creates missing
SLA rows, moves them PENDING -> DUE_SOON -> OVERDUE -> ESCALATED, raises
alerts on overdue/escalation, and resolves rows whose item left review.

Safe fallback: a timeout NEVER approves, activates or rejects anything. While
an item waits, the existing known-good adapter version and baseline stay in
force exactly as before; the SLA only makes the wait visible and escalates it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.event import Event
from app.db.models.governance import (
    SLA_DUE_SOON,
    SLA_ESCALATED,
    SLA_OPEN_STATES,
    SLA_OVERDUE,
    SLA_PENDING,
    SLA_RESOLVED,
    GovernanceSetting,
    ReviewSla,
)
from app.db.models.learning import OPEN_STATES as LEARNING_OPEN, LearningSession
from app.db.models.onboarding import ADAPTER_ACTIVE, OnboardedAdapter, OnboardingSession
from app.services import alert_service

DRIFT_EVENT = "DRIFT_EVENT"
ONBOARDING_SESSION = "ONBOARDING_SESSION"
LEARNING_SESSION = "LEARNING_SESSION"
ITEM_TYPES = (DRIFT_EVENT, ONBOARDING_SESSION, LEARNING_SESSION)
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
SETTINGS_KEY = "review_sla"
MAX_ESCALATIONS = 10


def policy(db: Session) -> dict[str, Any]:
    s = get_settings()
    base = {
        "hours": {"CRITICAL": s.sla_hours_critical, "HIGH": s.sla_hours_high,
                  "MEDIUM": s.sla_hours_medium, "LOW": s.sla_hours_low},
        # DUE_SOON once this fraction of the SLA window has elapsed.
        "due_soon_fraction": 0.75,
        # ESCALATED once overdue by this fraction of the SLA window; repeats each further interval.
        "escalate_after_fraction": 0.5,
    }
    row = db.get(GovernanceSetting, SETTINGS_KEY)
    if row is not None:
        value = row.value or {}
        base["hours"] = {**base["hours"], **(value.get("hours") or {})}
        for k in ("due_soon_fraction", "escalate_after_fraction"):
            if k in value:
                base[k] = value[k]
        base["updated_by"], base["updated_at"] = row.updated_by, row.updated_at
    return base


def validate_policy(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    hours = value.get("hours") or {}
    if not isinstance(hours, dict):
        raise ValueError("hours must be an object")
    clean_hours = {}
    for sev, h in hours.items():
        if sev not in SEVERITIES:
            raise ValueError(f"unknown severity '{sev}'")
        if not isinstance(h, (int, float)) or isinstance(h, bool) or not 0 < h <= 8760:
            raise ValueError(f"hours for {sev} must be a number in (0, 8760]")
        clean_hours[sev] = float(h)
    if clean_hours:
        out["hours"] = clean_hours
    for k in ("due_soon_fraction", "escalate_after_fraction"):
        if k in value:
            v = value[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 5:
                raise ValueError(f"{k} must be a number in (0, 5]")
            out[k] = float(v)
    unknown = set(value) - {"hours", "due_soon_fraction", "escalate_after_fraction"}
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    return out


def set_policy(db: Session, value: dict[str, Any], *, by: str) -> dict[str, Any]:
    clean = validate_policy(value)
    row = db.get(GovernanceSetting, SETTINGS_KEY)
    if row is None:
        row = GovernanceSetting(key=SETTINGS_KEY, value=clean, updated_by=by)
        db.add(row)
    else:
        merged = dict(row.value or {})
        if "hours" in clean:
            merged["hours"] = {**(merged.get("hours") or {}), **clean["hours"]}
        merged.update({k: v for k, v in clean.items() if k != "hours"})
        row.value = merged
        row.updated_by = by
    db.flush()
    return policy(db)


# --------------------------------------------------------------------------
# Open review items (read from the authoritative Phase 3/5/6 records)
# --------------------------------------------------------------------------


def _open_items(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for e in db.execute(select(Event.event_id, Event.received_at, Event.adapter_id, Event.processing_metadata)
                        .where(Event.status == "UNDER_REVIEW")).all():
        drift = (e.processing_metadata or {}).get("drift") or {}
        items.append({"item_type": DRIFT_EVENT, "item_id": e.event_id, "opened_at": e.received_at,
                      "source_key": drift.get("source_key") or e.adapter_id,
                      "severity": drift.get("severity") if drift.get("severity") in SEVERITIES else "MEDIUM"})
    for s in db.execute(select(OnboardingSession).where(OnboardingSession.status == "VALIDATED")).scalars():
        result = (s.validation or {}).get("result")
        items.append({"item_type": ONBOARDING_SESSION, "item_id": s.id,
                      "opened_at": s.created_at, "source_key": ((s.validation or {}).get("adapter_preview") or {}).get("id"),
                      "severity": "MEDIUM" if result == "PASSED" else "LOW"})
    for s in db.execute(select(LearningSession).where(LearningSession.status.in_(LEARNING_OPEN))).scalars():
        items.append({"item_type": LEARNING_SESSION, "item_id": s.id, "opened_at": s.created_at,
                      "source_key": s.source_key, "severity": s.risk if s.risk in SEVERITIES else "MEDIUM"})
    return items


def _fallback(db: Session, source_key: str | None, item_type: str) -> str:
    if item_type == ONBOARDING_SESSION:
        return "Nothing is activated on timeout; logs from this source keep being preserved (FAILED/unknown) until a human approves an adapter."
    if not source_key:
        return "No automatic action on timeout; the current configuration stays in force."
    active = db.execute(select(OnboardedAdapter.version).where(
        OnboardedAdapter.adapter_id == source_key, OnboardedAdapter.status == ADAPTER_ACTIVE)).scalar()
    version = f"v{active}" if active is not None else "the shipped version"
    return (f"No automatic promotion on timeout: adapter '{source_key}' {version} and its current baseline stay "
            "active; affected events remain UNDER_REVIEW with raw data preserved.")


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_status(row: ReviewSla, now: datetime, pol: dict[str, Any]) -> tuple[str, int]:
    """Status and total escalation level implied by elapsed time alone."""
    window = timedelta(hours=row.sla_hours)
    opened, due = _as_utc(row.opened_at), _as_utc(row.due_at)
    if now < opened + window * pol["due_soon_fraction"]:
        return SLA_PENDING, 0
    if now < due:
        return SLA_DUE_SOON, 0
    interval = window * pol["escalate_after_fraction"]
    level = int((now - due) / interval) if interval.total_seconds() > 0 else 0
    return (SLA_ESCALATED if level >= 1 else SLA_OVERDUE), min(level, MAX_ESCALATIONS)


def next_action(item_type: str, status: str) -> str:
    who = {DRIFT_EVENT: "SECURITY_ENGINEER: accept, add variant or acknowledge the drift",
           ONBOARDING_SESSION: "SECURITY_ENGINEER: approve or reject the validated adapter proposal",
           LEARNING_SESSION: "SECURITY_ENGINEER: review, approve/activate or reject the learning proposal"}[item_type]
    if status == SLA_ESCALATED:
        return f"ESCALATED to SOC_ADMIN — {who}"
    if status == SLA_OVERDUE:
        return f"OVERDUE — {who}"
    return who


def sweep(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(tz=timezone.utc)
    pol = policy(db)
    open_items = {(i["item_type"], i["item_id"]): i for i in _open_items(db)}
    rows = {(r.item_type, r.item_id): r for r in db.execute(select(ReviewSla)).scalars().all()}
    counts = {"created": 0, "resolved": 0, "reopened": 0, "overdue_alerts": 0, "escalations": 0}

    for key, item in open_items.items():
        row = rows.get(key)
        if row is None:
            hours = float(pol["hours"][item["severity"]])
            opened = _as_utc(item["opened_at"])
            row = ReviewSla(item_type=item["item_type"], item_id=item["item_id"], source_key=item["source_key"],
                            severity=item["severity"], opened_at=opened, sla_hours=hours,
                            due_at=opened + timedelta(hours=hours), status=SLA_PENDING, escalation_count=0)
            db.add(row)
            counts["created"] += 1
        elif row.status == SLA_RESOLVED:
            # Back in review (e.g. a reprocessed event drifted again): a new SLA window starts now.
            row.status, row.resolved_at, row.resolution = SLA_PENDING, None, None
            row.opened_at, row.due_at, row.escalation_count = now, now + timedelta(hours=row.sla_hours), 0
            counts["reopened"] += 1
        row.fallback = _fallback(db, row.source_key, row.item_type)
        status, level = compute_status(row, now, pol)
        previous = row.status
        row.status = status
        if status in (SLA_OVERDUE, SLA_ESCALATED) and previous not in (SLA_OVERDUE, SLA_ESCALATED):
            _alert(db, row, alert_service.REVIEW_OVERDUE, "HIGH" if row.severity in ("CRITICAL", "HIGH") else "MEDIUM")
            counts["overdue_alerts"] += 1
        if level > row.escalation_count:
            row.escalation_count = level
            row.last_escalated_at = now
            _alert(db, row, alert_service.REVIEW_ESCALATED, "CRITICAL" if row.severity == "CRITICAL" else "HIGH",
                   suffix=f":{level}")
            counts["escalations"] += 1

    for key, row in rows.items():
        if key not in open_items and row.status in SLA_OPEN_STATES:
            row.status = SLA_RESOLVED
            row.resolved_at = now
            row.resolution = "DECIDED" if _as_utc(row.due_at) >= now else "DECIDED_AFTER_DEADLINE"
            counts["resolved"] += 1
    db.commit()
    return counts


def _alert(db: Session, row: ReviewSla, kind: str, severity: str, suffix: str = "") -> None:
    label = {DRIFT_EVENT: "Drift review", ONBOARDING_SESSION: "Onboarding approval", LEARNING_SESSION: "Learning review"}[row.item_type]
    alert_service.publish(db, alert_service.AlertEvent(
        kind=kind, severity=severity,
        title=f"{label} {'escalated' if kind == alert_service.REVIEW_ESCALATED else 'overdue'}: {row.item_id}",
        message=f"{label} for {row.source_key or row.item_id} ({row.severity}) was due {row.due_at.isoformat()}. "
                f"{row.fallback}",
        dedup_key=f"{kind}:{row.item_type}:{row.item_id}{suffix}", object_type=row.item_type, object_id=row.item_id,
        details={"due_at": row.due_at.isoformat(), "severity": row.severity, "escalation_count": row.escalation_count},
    ), commit=False)


def row_dict(row: ReviewSla, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(tz=timezone.utc)
    opened = _as_utc(row.opened_at)
    end = _as_utc(row.resolved_at) if row.resolved_at else now
    return {
        "item_type": row.item_type, "item_id": row.item_id, "source_key": row.source_key, "severity": row.severity,
        "opened_at": row.opened_at, "due_at": row.due_at, "sla_hours": row.sla_hours, "status": row.status,
        "review_age_seconds": int((end - opened).total_seconds()),
        "seconds_to_deadline": int((_as_utc(row.due_at) - now).total_seconds()),
        "escalation_count": row.escalation_count, "last_escalated_at": row.last_escalated_at,
        "resolved_at": row.resolved_at, "resolution": row.resolution,
        "next_action": None if row.status == SLA_RESOLVED else next_action(row.item_type, row.status),
        "fallback": row.fallback,
    }


def list_rows(db: Session, *, item_type: str | None = None, item_id: str | None = None,
              include_resolved: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(ReviewSla)
    if item_type:
        stmt = stmt.where(ReviewSla.item_type == item_type)
    if item_id:
        stmt = stmt.where(ReviewSla.item_id == item_id)
    if not include_resolved:
        stmt = stmt.where(ReviewSla.status.in_(SLA_OPEN_STATES))
    rows = db.execute(stmt.order_by(ReviewSla.due_at).limit(limit)).scalars().all()
    now = datetime.now(tz=timezone.utc)
    return [row_dict(r, now) for r in rows]


def stats(db: Session) -> dict[str, Any]:
    by_status = dict(db.execute(select(ReviewSla.status, func.count()).group_by(ReviewSla.status)).all())
    by_type = dict(db.execute(select(ReviewSla.item_type, func.count())
                              .where(ReviewSla.status.in_(SLA_OPEN_STATES)).group_by(ReviewSla.item_type)).all())
    return {"by_status": by_status, "open_by_type": by_type,
            "open": sum(v for k, v in by_status.items() if k in SLA_OPEN_STATES)}
