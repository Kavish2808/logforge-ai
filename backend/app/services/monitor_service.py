"""Condition monitor feeding the alert bus.

Each check reads stored state only and publishes a deduplicated alert when a
configured condition holds. Thresholds are runtime-tunable by SOC_ADMIN
(governance setting "alert_thresholds").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.evidence import RAW_FAILED, EventExtensionOverflow, EventRawStorage
from app.db.models.governance import GovernanceSetting
from app.db.models.onboarding import ADAPTER_ACTIVE, ADAPTER_SUPERSEDED, OnboardedAdapter
from app.db.repository.views_repo import DRIFT_SEVERITY, DRIFT_SOURCE
from app.services import alert_service, audit_service, evidence_service
from app.services.alert_service import AlertEvent

SETTINGS_KEY = "alert_thresholds"
DEFAULTS: dict[str, float] = {
    "parser_failure_window_minutes": 15,
    "parser_failure_min_events": 20,
    "parser_failure_rate": 0.5,
    "overflow_window_minutes": 60,
    "overflow_events_threshold": 100,
    "learning_regression_min_events": 20,
    "learning_regression_delta": 0.2,
}


def thresholds(db: Session) -> dict[str, float]:
    row = db.get(GovernanceSetting, SETTINGS_KEY)
    return {**DEFAULTS, **((row.value or {}) if row else {})}


def validate_thresholds(value: dict[str, Any]) -> dict[str, float]:
    unknown = set(value) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown thresholds: {sorted(unknown)}")
    out = {}
    for k, v in value.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 or v > 1_000_000:
            raise ValueError(f"{k} must be a positive number")
        if k.endswith("_rate") or k.endswith("_delta"):
            if v > 1:
                raise ValueError(f"{k} must be in (0, 1]")
        out[k] = float(v)
    return out


def set_thresholds(db: Session, value: dict[str, Any], *, by: str) -> dict[str, float]:
    clean = validate_thresholds(value)
    row = db.get(GovernanceSetting, SETTINGS_KEY)
    if row is None:
        db.add(GovernanceSetting(key=SETTINGS_KEY, value=clean, updated_by=by))
    else:
        row.value = {**(row.value or {}), **clean}
        row.updated_by = by
    db.flush()
    return thresholds(db)


def _publish(db: Session, **kw: Any) -> None:
    alert_service.publish(db, AlertEvent(**kw), commit=False)


def check_critical_drift(db: Session) -> int:
    rows = db.execute(
        select(DRIFT_SOURCE, func.count()).select_from(Event)
        .where(Event.status == "UNDER_REVIEW", DRIFT_SEVERITY == "CRITICAL").group_by(DRIFT_SOURCE)).all()
    for source, n in rows:
        _publish(db, kind=alert_service.CRITICAL_DRIFT, severity="CRITICAL",
                 title=f"Critical drift awaiting review: {source}",
                 message=f"{n} event(s) from '{source}' are UNDER_REVIEW with CRITICAL drift severity. "
                         "The active adapter and baseline stay in force until a human decides.",
                 dedup_key=f"CRITICAL_DRIFT:{source}", object_type="source", object_id=source,
                 details={"under_review_critical": n})
    return len(rows)


def check_parser_failures(db: Session, t: dict[str, float], now: datetime) -> int:
    since = now - timedelta(minutes=t["parser_failure_window_minutes"])
    total, failed = db.execute(select(func.count(), func.count().filter(Event.status == "FAILED"))
                               .where(Event.received_at >= since)).one()
    if total >= t["parser_failure_min_events"] and failed / total >= t["parser_failure_rate"]:
        _publish(db, kind=alert_service.PARSER_FAILURE_SPIKE, severity="HIGH",
                 title="Parser failure spike",
                 message=f"{failed}/{total} events received in the last {int(t['parser_failure_window_minutes'])} min "
                         "FAILED to parse. Raw events are preserved; consider onboarding.",
                 dedup_key="PARSER_FAILURE_SPIKE", details={"failed": failed, "total": total, "since": since.isoformat()})
        return 1
    return 0


def check_overflow(db: Session, t: dict[str, float], now: datetime) -> int:
    since = now - timedelta(minutes=t["overflow_window_minutes"])
    n = db.execute(select(func.count()).select_from(EventExtensionOverflow)
                   .where(EventExtensionOverflow.created_at >= since)).scalar_one()
    if n >= t["overflow_events_threshold"]:
        _publish(db, kind=alert_service.EXTENSION_OVERFLOW, severity="MEDIUM",
                 title="Extension overflow threshold exceeded",
                 message=f"{n} event(s) spilled extensions beyond the inline budget in the last "
                         f"{int(t['overflow_window_minutes'])} min. All fields are preserved in overflow storage.",
                 dedup_key="EXTENSION_OVERFLOW", details={"spilled_events": n, "since": since.isoformat()})
        return 1
    return 0


def check_raw_vault(db: Session) -> int:
    n = db.execute(select(func.count()).select_from(EventRawStorage).where(
        EventRawStorage.status == RAW_FAILED, EventRawStorage.backend != "disabled")).scalar_one()
    if n:
        _publish(db, kind=alert_service.RAW_VAULT_FAILURE, severity="HIGH", title="Raw vault writes failing",
                 message=f"{n} event(s) have no cold raw copy because the vault write failed. The raw payload is "
                         "still preserved in PostgreSQL; writes are retried by the scheduler.",
                 dedup_key="RAW_VAULT_FAILURE", details={"failed": n})
        return 1
    return 0


def _quality(db: Session, adapter_id: str, version: int) -> tuple[int, int]:
    total, bad = db.execute(select(func.count(), func.count().filter(Event.status.in_(("PARTIAL", "FAILED", "UNDER_REVIEW"))))
                            .where(Event.adapter_id == adapter_id, Event.adapter_version == str(version))).one()
    return total, bad


def check_learning_regression(db: Session, t: dict[str, float]) -> int:
    """A learned (Phase 6) version whose production partial/failed/review rate
    is materially worse than the version it superseded."""
    found = 0
    for row in db.execute(select(OnboardedAdapter).where(OnboardedAdapter.status == ADAPTER_ACTIVE)).scalars():
        summary = row.validation_summary or {}
        if summary.get("origin") != "phase6_learning":
            continue
        previous = summary.get("learned_from_version")
        prev_row = db.execute(select(OnboardedAdapter).where(
            OnboardedAdapter.adapter_id == row.adapter_id, OnboardedAdapter.version == previous,
            OnboardedAdapter.status == ADAPTER_SUPERSEDED)).scalars().first()
        if prev_row is None:
            continue
        n_new, bad_new = _quality(db, row.adapter_id, row.version)
        n_old, bad_old = _quality(db, row.adapter_id, prev_row.version)
        if min(n_new, n_old) < t["learning_regression_min_events"]:
            continue
        new_rate, old_rate = bad_new / n_new, bad_old / n_old
        if new_rate - old_rate >= t["learning_regression_delta"]:
            found += 1
            _publish(db, kind=alert_service.LEARNING_REGRESSION, severity="HIGH",
                     title=f"Learning regression: {row.adapter_id} v{row.version}",
                     message=f"v{row.version} has {new_rate:.0%} partial/failed/under-review events vs {old_rate:.0%} "
                             f"for v{prev_row.version}. Consider a human rollback (not automatic).",
                     dedup_key=f"LEARNING_REGRESSION:{row.adapter_id}:{row.version}", object_type="adapter",
                     object_id=row.adapter_id, details={"new_rate": round(new_rate, 4), "old_rate": round(old_rate, 4),
                                                        "new_events": n_new, "old_events": n_old})
    return found


def check_integrity(db: Session) -> dict[str, Any]:
    chain = evidence_service.verify_chain(db, recent=20)
    if not chain["valid"]:
        _publish(db, kind=alert_service.INTEGRITY_FAILURE, severity="CRITICAL", title="Evidence chain verification failed",
                 message=f"{len(chain['problems'])} problem(s) found in the Merkle evidence chain: "
                         + ", ".join(sorted({p['problem'] for p in chain['problems']})),
                 dedup_key="INTEGRITY_FAILURE:merkle", details={"problems": chain["problems"][:10]})
    audit = audit_service.verify(db, recent=1000)
    if not audit["valid"]:
        _publish(db, kind=alert_service.AUDIT_CHAIN_BROKEN, severity="CRITICAL", title="Audit log chain broken",
                 message=f"Audit hash chain verification failed at seq {audit['first_break_seq']}.",
                 dedup_key="AUDIT_CHAIN_BROKEN", details={"problems": audit["problems"][:10]})
    return {"merkle_valid": chain["valid"], "audit_valid": audit["valid"]}


def sweep(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(tz=timezone.utc)
    t = thresholds(db)
    out = {
        "critical_drift_sources": check_critical_drift(db),
        "parser_failure_spike": check_parser_failures(db, t, now),
        "extension_overflow": check_overflow(db, t, now),
        "raw_vault_failure": check_raw_vault(db),
        "learning_regressions": check_learning_regression(db, t),
    }
    db.commit()
    out["integrity"] = check_integrity(db)
    db.commit()
    return out
