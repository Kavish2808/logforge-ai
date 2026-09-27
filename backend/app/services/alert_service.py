"""Internal alert bus: publish -> persist (deduplicated) -> deliver.

An alert is keyed by a dedup_key; while an alert with that key is OPEN a
repeat only bumps `occurrences` / `last_seen_at` (and marks it unread
again) rather than creating noise. Acknowledging closes it; a later repeat
opens a new alert.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts.channels import AlertPayload, configured_channels
from app.core.ids import generate_event_id
from app.db.models.governance import ALERT_ACKNOWLEDGED, ALERT_OPEN, Alert

logger = logging.getLogger(__name__)

SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

# Alert kinds produced by Phase 7.
CRITICAL_DRIFT = "CRITICAL_DRIFT"
REVIEW_OVERDUE = "REVIEW_OVERDUE"
REVIEW_ESCALATED = "REVIEW_ESCALATED"
LEARNING_REGRESSION = "LEARNING_REGRESSION"
INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
RAW_VAULT_FAILURE = "RAW_VAULT_FAILURE"
EXTENSION_OVERFLOW = "EXTENSION_OVERFLOW"
PARSER_FAILURE_SPIKE = "PARSER_FAILURE_SPIKE"
AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN"
KINDS = (CRITICAL_DRIFT, REVIEW_OVERDUE, REVIEW_ESCALATED, LEARNING_REGRESSION, INTEGRITY_FAILURE,
         RAW_VAULT_FAILURE, EXTENSION_OVERFLOW, PARSER_FAILURE_SPIKE, AUDIT_CHAIN_BROKEN)


class AlertNotFound(Exception):
    pass


@dataclass
class AlertEvent:
    kind: str
    severity: str
    title: str
    message: str
    dedup_key: str
    object_type: str | None = None
    object_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def publish(db: Session, event: AlertEvent, *, commit: bool = True, deliver: bool = True) -> Alert:
    if event.severity not in SEVERITIES:
        raise ValueError(f"unknown alert severity {event.severity!r}")
    now = datetime.now(tz=timezone.utc)
    existing = db.execute(
        select(Alert).where(Alert.dedup_key == event.dedup_key, Alert.status == ALERT_OPEN).with_for_update()
    ).scalars().first()
    if existing is not None:
        existing.occurrences += 1
        existing.last_seen_at = now
        existing.read = False
        existing.details = {**(existing.details or {}), **event.details}
        if commit:
            db.commit()
        return existing
    alert = Alert(
        id=generate_event_id(), kind=event.kind, severity=event.severity, title=event.title[:256],
        message=event.message, object_type=event.object_type, object_id=event.object_id,
        dedup_key=event.dedup_key[:256], details=event.details, status=ALERT_OPEN, read=False,
        occurrences=1, deliveries=[{"channel": "internal", "ok": True, "at": now.isoformat()}],
        created_at=now, last_seen_at=now,
    )
    db.add(alert)
    db.flush()
    if deliver:
        _deliver(alert)
    if commit:
        db.commit()
    return alert


def _deliver(alert: Alert) -> None:
    payload = AlertPayload(alert.id, alert.kind, alert.severity, alert.title, alert.message,
                           alert.object_type, alert.object_id, alert.details or {})
    results = list(alert.deliveries or [])
    for channel in configured_channels():
        at = datetime.now(tz=timezone.utc).isoformat()
        try:
            channel.deliver(payload)
            results.append({"channel": channel.name, "ok": True, "at": at})
        except Exception as exc:  # noqa: BLE001 — delivery must never break the caller
            logger.warning("Alert %s delivery via %s failed (%s).", alert.id, channel.name, type(exc).__name__)
            results.append({"channel": channel.name, "ok": False, "at": at, "error": type(exc).__name__})
    alert.deliveries = results


def acknowledge(db: Session, alert_id: str, *, by: str) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFound(alert_id)
    if alert.status != ALERT_ACKNOWLEDGED:
        alert.status = ALERT_ACKNOWLEDGED
        alert.acknowledged_by = by
        alert.acknowledged_at = datetime.now(tz=timezone.utc)
    alert.read = True
    db.commit()
    return alert


def mark_read(db: Session, alert_id: str, read: bool = True) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFound(alert_id)
    alert.read = read
    db.commit()
    return alert


def list_alerts(db: Session, *, status: str | None, kind: str | None, unread: bool | None, limit: int, offset: int):
    stmt = select(Alert)
    count = select(func.count()).select_from(Alert)
    for cond in (
        Alert.status == status if status else None,
        Alert.kind == kind if kind else None,
        Alert.read.is_(False) if unread else None,
    ):
        if cond is not None:
            stmt, count = stmt.where(cond), count.where(cond)
    rows = db.execute(stmt.order_by(Alert.last_seen_at.desc()).limit(limit).offset(offset)).scalars().all()
    return list(rows), db.execute(count).scalar_one()


def counts(db: Session) -> dict[str, Any]:
    by_status = dict(db.execute(select(Alert.status, func.count()).group_by(Alert.status)).all())
    unread = db.execute(select(func.count()).select_from(Alert).where(Alert.read.is_(False))).scalar_one()
    open_by_severity = dict(db.execute(
        select(Alert.severity, func.count()).where(Alert.status == ALERT_OPEN).group_by(Alert.severity)).all())
    open_by_kind = dict(db.execute(
        select(Alert.kind, func.count()).where(Alert.status == ALERT_OPEN).group_by(Alert.kind)).all())
    return {"by_status": by_status, "unread": unread, "open_by_severity": open_by_severity, "open_by_kind": open_by_kind}


def to_dict(a: Alert) -> dict[str, Any]:
    return {"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "message": a.message,
            "object_type": a.object_type, "object_id": a.object_id, "details": a.details, "status": a.status,
            "read": a.read, "occurrences": a.occurrences, "deliveries": a.deliveries,
            "acknowledged_by": a.acknowledged_by, "acknowledged_at": a.acknowledged_at,
            "created_at": a.created_at, "last_seen_at": a.last_seen_at}
