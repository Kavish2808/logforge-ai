"""FastAPI application entrypoint."""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import (
    alerts,
    auth,
    confidence,
    demo,
    drift,
    events,
    export,
    governance,
    health,
    ingest,
    integrity,
    learning,
    onboarding,
    views,
)
from app.config import get_settings
from app.core.logging import configure_logging
from app.governance.policy import governed
from app.phase8.register import register_all as register_phase8
from app.schema.errors import ErrorDetail, ErrorResponse
from app.services import scheduler

settings = get_settings()
configure_logging(debug=settings.debug)

logger = logging.getLogger(__name__)

# `debug=False` always: Starlette's debug mode renders unhandled exceptions
# as an HTML page containing the full stack trace, which must never reach
# an API client. `settings.debug` still controls logging verbosity and
# uvicorn's --reload behavior in docker-compose, just not this.
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Phase 7 background work (seal, SLA sweep, alert sweep, vault retry).
    # SCHEDULER_ENABLED=false disables it (the test suite does).
    if settings.rbac_mode != "enforce":
        logger.warning("RBAC_MODE=%s: governance actions are accepted anonymously (audited as 'anonymous'). "
                       "Development/demo only; APP_ENV=production refuses to start without RBAC_MODE=enforce.",
                       settings.rbac_mode)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(title=settings.app_name, debug=False, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = {".".join(str(p) for p in err["loc"] if p != "body"): err["msg"] for err in exc.errors()}
    body = ErrorResponse(error=ErrorDetail(code="VALIDATION_ERROR", message="Request validation failed", fields=fields))
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body.model_dump())


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Registered on Starlette's base HTTPException (not fastapi.HTTPException,
    # a subclass) so this also catches routing-level errors Starlette raises
    # itself — 404 for unmatched routes, 405 Method Not Allowed, etc. — not
    # just the ones our own route handlers raise.
    code = {
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
        status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_422_UNPROCESSABLE_ENTITY: "UNPROCESSABLE",
        status.HTTP_429_TOO_MANY_REQUESTS: "TOO_MANY_REQUESTS",
        status.HTTP_502_BAD_GATEWAY: "UPSTREAM_ERROR",
    }.get(exc.status_code, "HTTP_ERROR")
    body = ErrorResponse(error=ErrorDetail(code=code, message=str(exc.detail)))
    # Keep protocol headers such as Retry-After (429) and WWW-Authenticate.
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(), headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Full details go to the server log only. The client gets a generic,
    # safe message — never a stack trace, file path, or DB error string.
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url.path)
    body = ErrorResponse(
        error=ErrorDetail(code="INTERNAL_ERROR", message="An unexpected error occurred. Please try again.")
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body.model_dump())


# Phase 7 governance (RBAC, identity binding, maker-checker, hash-chained
# audit) is applied to the existing mutating endpoints as a router-level
# dependency: the Phase 3/5/6 routers and services themselves are unchanged.
GOVERNED = [Depends(governed)]

# Phase 8: attach guards / scheduler steps / persist hooks through the approved
# Phase 7 registries (idempotent; nothing is attached when PHASE8_ENABLED=false).
register_phase8()

app.include_router(health.router)
app.include_router(ingest.router, prefix=settings.api_v1_prefix)
app.include_router(events.router, prefix=settings.api_v1_prefix, dependencies=GOVERNED)
app.include_router(drift.router, prefix=settings.api_v1_prefix, dependencies=GOVERNED)
app.include_router(onboarding.router, prefix=settings.api_v1_prefix, dependencies=GOVERNED)
app.include_router(learning.router, prefix=settings.api_v1_prefix, dependencies=GOVERNED)
app.include_router(views.router, prefix=settings.api_v1_prefix)
app.include_router(demo.router, prefix=settings.api_v1_prefix, dependencies=GOVERNED)
# Phase 7 routers
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(governance.router, prefix=settings.api_v1_prefix)
app.include_router(integrity.router, prefix=settings.api_v1_prefix)
app.include_router(alerts.router, prefix=settings.api_v1_prefix)
app.include_router(export.router, prefix=settings.api_v1_prefix)
app.include_router(confidence.router, prefix=settings.api_v1_prefix)


@app.get("/")
def root() -> dict:
    return {"service": settings.app_name, "status": "running"}
