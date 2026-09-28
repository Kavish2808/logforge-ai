"""Operational Dashboard API endpoint."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.event import Event
from app.db.models.governance import Alert
from app.db.repository import views_repo as repo
from app.services import views_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
@router.get("/api/v1/dashboard")
def get_dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    f = repo.EventFilters()
    summary_data = views_service.summary(db, f, "hour")
    totals = summary_data.get("totals", {})
    by_status = summary_data.get("by_status", {})
    by_format = summary_data.get("by_format", {})
    trend = summary_data.get("trend", [])

    # Format counts list for donut chart
    formats = [{"name": str(k).upper(), "count": v} for k, v in by_format.items() if k]
    if not formats and totals.get("events", 0) > 0:
        formats = [{"name": "UNKNOWN", "count": totals.get("events", 0)}]

    # Throughput points for SVG activity chart
    throughput = [{"time": item.get("bucket"), "count": item.get("count", 0)} for item in trend]

    # Recent events
    recent_events_rows = db.scalars(
        select(Event).order_by(desc(Event.received_at)).limit(10)
    ).all()
    recent_events = [
        {
            "id": e.event_id,
            "source": e.adapter_id or e.vendor or "unassigned",
            "format": (e.format_detected or "unknown").upper(),
            "status": e.status,
            "vendor": e.vendor or "Generic",
            "revision": 1,
            "created_at": e.received_at.isoformat() if e.received_at else None,
        }
        for e in recent_events_rows
    ]

    # Recent alerts
    recent_alerts_rows = db.scalars(
        select(Alert).order_by(desc(Alert.created_at)).limit(5)
    ).all()
    recent_alerts = [
        {
            "id": a.id,
            "title": a.title,
            "kind": a.kind,
            "severity": a.severity,
            "message": a.message,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "acknowledged": a.status == "ACKNOWLEDGED",
        }
        for a in recent_alerts_rows
    ]

    return {
        "counts": {
            "events": totals.get("events", 0),
            "parsed": by_status.get("SUCCESS", 0),
            "partial": by_status.get("PARTIAL", 0),
            "failed": by_status.get("FAILED", 0),
            "drifts": totals.get("drift_events", 0),
            "pending_reviews": totals.get("pending_reviews", 0),
            "integrity_failures": 0,
        },
        "throughput": throughput,
        "formats": formats,
        "recent_events": recent_events,
        "recent_alerts": recent_alerts,
    }
