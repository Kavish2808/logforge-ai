"""Read-only operational intelligence API (/api/v1/views).

Every request runs in a READ ONLY database transaction: this layer cannot
write, and it never parses, normalizes, learns or activates anything."""
from collections.abc import Generator
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.repository import views_repo as repo
from app.schema.views import EventPage, FilterValues, Lineage, SourceDetail, SourceList, Summary, Timeline
from app.services import views_service as svc

router = APIRouter(prefix="/views", tags=["views"])


def get_readonly_db(db: Session = Depends(get_db)) -> Generator[Session, None, None]:
    db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        yield db
    finally:
        db.rollback()


def _filters(
    start: datetime | None = Query(default=None, description="received_at >= start (ISO 8601)"),
    end: datetime | None = Query(default=None, description="received_at < end (ISO 8601)"),
    status: list[str] | None = Query(default=None),
    source: str | None = Query(default=None, max_length=128, description="Source key (adapter id)"),
    vendor: str | None = Query(default=None, max_length=128),
    product: str | None = Query(default=None, max_length=128),
    format: str | None = Query(default=None, max_length=32),
    adapter_id: str | None = Query(default=None, max_length=128),
    adapter_version: str | None = Query(default=None, max_length=32),
    drift_status: str | None = Query(default=None, max_length=32, description="NORMAL, DRIFT, ..., or NONE"),
    drift_severity: str | None = Query(default=None, max_length=16),
    severity: str | None = Query(default=None, max_length=32),
    category: str | None = Query(default=None, max_length=128, description="OCSF category name"),
    search: str | None = Query(default=None, min_length=3, max_length=200,
                               description="event_id, raw SHA-256, or a substring of the raw event"),
) -> repo.EventFilters:
    if start and end and start >= end:
        raise HTTPException(status_code=422, detail="'start' must be earlier than 'end'.")
    return repo.EventFilters(start=start, end=end, status=status, source=source, vendor=vendor, product=product,
                             format=format, adapter_id=adapter_id, adapter_version=adapter_version,
                             drift_status=drift_status, drift_severity=drift_severity, severity=severity,
                             category=category, search=search)


@router.get("/events", response_model=EventPage)
def events(
    f: repo.EventFilters = Depends(_filters),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    include_total: bool = False,
    db: Session = Depends(get_readonly_db),
) -> EventPage:
    try:
        rows, next_cursor, has_more = repo.event_page(db, f, limit=limit, cursor=cursor)
    except repo.CursorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return EventPage(items=[svc.event_row(r) for r in rows], limit=limit, next_cursor=next_cursor,
                     has_more=has_more, total=repo.count_events(db, f) if include_total else None)


@router.get("/summary", response_model=Summary)
def summary(
    f: repo.EventFilters = Depends(_filters),
    bucket: Literal["hour", "day"] = "day",
    db: Session = Depends(get_readonly_db),
) -> Summary:
    return Summary(**svc.summary(db, f, bucket))


@router.get("/filters", response_model=FilterValues)
def filters(db: Session = Depends(get_readonly_db)) -> FilterValues:
    return FilterValues(**svc.filter_values(db))


@router.get("/events/{event_id}/lineage", response_model=Lineage)
def lineage(event_id: str, db: Session = Depends(get_readonly_db)) -> Lineage:
    try:
        return Lineage(**svc.lineage(db, event_id))
    except svc.ViewsNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.get("/sources", response_model=SourceList)
def sources(db: Session = Depends(get_readonly_db)) -> SourceList:
    items = svc.list_sources(db)
    return SourceList(total=len(items), items=items)


@router.get("/sources/{source_key}", response_model=SourceDetail)
def source(source_key: str, db: Session = Depends(get_readonly_db)) -> SourceDetail:
    try:
        return SourceDetail(**svc.source_detail(db, source_key))
    except svc.ViewsNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.get("/sources/{source_key}/timeline", response_model=Timeline)
def source_timeline(source_key: str, db: Session = Depends(get_readonly_db)) -> Timeline:
    try:
        return Timeline(**svc.timeline(db, source_key))
    except svc.ViewsNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
