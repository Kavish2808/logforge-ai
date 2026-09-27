"""Export + integration contract (logforge.export.v1).

GET  /export/events  — filters as query parameters (same names as /views/events)
POST /export/events  — the same selection as a JSON body (large event_id lists)
GET  /export/schema  — the published, versioned output schema
Both export endpoints stream in bounded batches; nothing is loaded unbounded.
"""
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.views import _filters
from app.config import get_settings
from app.db.repository import views_repo as repo
from app.export.schema import SCHEMA_VERSION, published
from app.governance import roles
from app.governance.deps import require
from app.schema.trust import ULID_PATTERN, ExportRequest
from app.services import audit_service, export_service
from app.services.auth_service import Actor

router = APIRouter(prefix="/export", tags=["export"])

MEDIA = {"ndjson": "application/x-ndjson", "json": "application/json"}


@router.get("/schema")
def schema() -> dict[str, Any]:
    return published()


@router.get("/logs")
def logs(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": export_service.list_logs(db, limit), "stats": export_service.stats(db)}


def _start(db: Session, actor: Actor, *, fmt: str, f: repo.EventFilters, event_ids: list[str] | None,
           cursor: str | None, limit: int | None, include_raw: bool) -> StreamingResponse:
    settings = get_settings()
    cap = settings.export_json_max_events if fmt == "json" else settings.export_max_events
    max_events = min(limit or cap, cap)
    if cursor:
        try:
            repo.decode_cursor(cursor)
        except repo.CursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    filters = export_service.filters_dict(f, event_ids)
    log = export_service.start_log(db, actor=actor.username, role=actor.role, fmt=fmt, filters=filters,
                                   max_events=max_events, include_raw=include_raw)
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                         action="EXPORT", object_type="export", object_id=log.id,
                         details={"format": fmt, "filters": filters, "max_events": max_events, "include_raw": include_raw,
                                  "cursor": bool(cursor)})
    body = export_service.stream(db.get_bind(), export_id=log.id, fmt=fmt, f=f, event_ids=event_ids, cursor=cursor,
                                 max_events=max_events, batch_size=settings.export_batch_size,
                                 include_raw=include_raw, filters_echo=filters)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(body, media_type=MEDIA[fmt], headers={
        "Content-Disposition": f'attachment; filename="logforge-export-{stamp}.{fmt}"',
        "X-LogForge-Export-Id": log.id, "X-LogForge-Schema-Version": SCHEMA_VERSION,
        "X-LogForge-Max-Events": str(max_events)})


@router.get("/events")
def export_get(
    f: repo.EventFilters = Depends(_filters),
    output: Literal["ndjson", "json"] = Query(default="ndjson", description="Output format (`format` is the log-format filter)."),
    limit: int | None = Query(default=None, ge=1, le=1_000_000),
    cursor: str | None = Query(default=None, max_length=512),
    include_raw: bool = False,
    event_id: list[str] | None = Query(default=None, max_length=200, description="Restrict to these event ids."),
    actor: Actor = Depends(require(roles.EXPORT)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    import re

    for i in event_id or []:
        if not re.match(ULID_PATTERN, i):
            raise HTTPException(status_code=422, detail=f"invalid event id {i[:40]!r}")
    return _start(db, actor, fmt=output, f=f, event_ids=event_id, cursor=cursor, limit=limit, include_raw=include_raw)


@router.post("/events")
def export_post(request: ExportRequest, actor: Actor = Depends(require(roles.EXPORT)),
                db: Session = Depends(get_db)) -> StreamingResponse:
    f = repo.EventFilters(start=request.start, end=request.end, status=request.status, source=request.source,
                          vendor=request.vendor, product=request.product, format=request.format,
                          adapter_id=request.adapter_id, adapter_version=request.adapter_version,
                          drift_status=request.drift_status, drift_severity=request.drift_severity,
                          severity=request.severity, category=request.category, search=request.search)
    return _start(db, actor, fmt=request.output, f=f, event_ids=request.event_ids or None, cursor=request.cursor,
                  limit=request.limit, include_raw=request.include_raw)
