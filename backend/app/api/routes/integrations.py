"""Webhook integration registry with destination policy & SSRF protection.

Contract (truthful by design):
- Registering an integration only stores its destination. Outbound evidence delivery is NOT
  implemented in this release: `POST .../deliver` sends nothing and answers 501 NOT_IMPLEMENTED
  (it never reports a delivery that did not happen). Use the export API for evidence transfer.
- Every route requires an authenticated SOC_ADMIN (`governance` capability) in every RBAC mode,
  and every create / delivery request is written to the tamper-evident audit log.
- Storage is a local JSON file on the API node (single-node configuration; not replicated to
  other replicas, not in the database, not included in backups).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.governance import roles
from app.governance.deps import require
from app.services import audit_service
from app.services.auth_service import Actor

logger = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])

_LOCK = threading.Lock()
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
_DATA_PATH = os.path.join(_DATA_DIR, "integrations.json")

STATUS_CONFIGURED = "CONFIGURED"
DELIVERY_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def _load() -> list[dict[str, Any]]:
    if not os.path.exists(_DATA_PATH):
        return []
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def configured_count() -> int:
    """Number of registered integrations on this node (dashboard)."""
    with _LOCK:
        return len(_load())


class IntegrationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1, max_length=2048)


def validate_destination(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("Invalid URL scheme: only http/https supported")
    hostname = parts.hostname
    if not hostname:
        raise ValueError("Missing destination hostname")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            info = socket.getaddrinfo(hostname, None)
            ip = ipaddress.ip_address(info[0][4][0])
        except Exception as exc:
            raise ValueError(f"Could not resolve destination: {exc}")
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        raise ValueError("Private, loopback, link-local, and multicast destinations are forbidden")


def _audit(db: Session, actor: Actor, action: str, object_id: str | None, decision: str, details: dict[str, Any]) -> None:
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated, action=action,
                         object_type="integration", object_id=object_id, decision=decision, details=details)


@router.get("/integrations")
@router.get("/api/v1/integrations")
def list_integrations(actor: Actor = Depends(require(roles.GOVERNANCE))) -> list[dict[str, Any]]:
    with _LOCK:
        return _load()


@router.post("/integrations", status_code=201)
@router.post("/api/v1/integrations", status_code=201)
def create_integration(body: IntegrationCreate, actor: Actor = Depends(require(roles.GOVERNANCE)),
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        validate_destination(body.url)
    except ValueError as exc:
        _audit(db, actor, "INTEGRATION_CREATE", None, audit_service.DENIED,
               {"name": body.name, "reason": str(exc)[:300]})
        raise HTTPException(status_code=422, detail=str(exc))

    item = {
        "id": f"int_{uuid.uuid4().hex[:12]}",
        "name": body.name,
        "url": body.url,
        "status": STATUS_CONFIGURED,
        "delivery": DELIVERY_NOT_IMPLEMENTED,
        "created_by": actor.username,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _LOCK:
        items = _load()
        items.append(item)
        _save(items)
    _audit(db, actor, "INTEGRATION_CREATE", item["id"], audit_service.SUCCESS, {"name": body.name})
    return item


@router.post("/integrations/{integration_id}/deliver")
@router.post("/api/v1/integrations/{integration_id}/deliver")
def deliver_integration(integration_id: str, actor: Actor = Depends(require(roles.GOVERNANCE)),
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    with _LOCK:
        items = _load()
        match = next((i for i in items if i["id"] == integration_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Integration not found")
    reason = (f"Outbound evidence delivery is not implemented in this release; nothing was sent to "
              f"'{match['name']}'. Use GET /api/v1/export/events to transfer evidence.")
    _audit(db, actor, "INTEGRATION_DELIVERY", integration_id, audit_service.FAILED,
           {"delivery": DELIVERY_NOT_IMPLEMENTED, "reason": reason})
    raise HTTPException(status_code=501, detail=reason)
