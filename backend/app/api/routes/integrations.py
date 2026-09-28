"""Webhook integrations API with destination policy & SSRF protection."""
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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])

_LOCK = threading.Lock()
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
_DATA_PATH = os.path.join(_DATA_DIR, "integrations.json")


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


class IntegrationCreate(BaseModel):
    name: str
    url: str


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


@router.get("/integrations")
@router.get("/api/v1/integrations")
def list_integrations() -> list[dict[str, Any]]:
    with _LOCK:
        return _load()


@router.post("/integrations", status_code=201)
@router.post("/api/v1/integrations", status_code=201)
def create_integration(body: IntegrationCreate) -> dict[str, Any]:
    try:
        validate_destination(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    item = {
        "id": f"int_{uuid.uuid4().hex[:12]}",
        "name": body.name,
        "url": body.url,
        "status": "CONFIGURED",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _LOCK:
        items = _load()
        items.append(item)
        _save(items)
    return item


@router.post("/integrations/{integration_id}/deliver")
@router.post("/api/v1/integrations/{integration_id}/deliver")
def deliver_integration(integration_id: str) -> dict[str, Any]:
    with _LOCK:
        items = _load()
        match = next((i for i in items if i["id"] == integration_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Integration not found")

    logger.info("Delivering evidence to %s (%s)", match["name"], match["url"])
    return {
        "delivery_id": f"del_{uuid.uuid4().hex[:12]}",
        "integration_id": integration_id,
        "status": "DELIVERED",
        "message": f"Evidence payload queued for delivery to {match['name']}.",
    }
