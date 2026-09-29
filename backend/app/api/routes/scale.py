"""Scale, high-throughput metrics, and stress benchmark endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import scale_service

router = APIRouter(tags=["scale"])


class BenchmarkRequest(BaseModel):
    count: int = Field(default=1000, ge=10, le=1000000)
    target_eps: int = Field(default=5000, ge=100, le=100000)
    vendor_mix: list[str] | None = Field(default=None)


@router.get("/scale/metrics")
@router.get("/api/scale/metrics")
@router.get("/api/v1/scale/metrics")
def get_metrics() -> dict[str, Any]:
    """Retrieve live events-per-second, latency percentiles, and scale indicators."""
    return scale_service.get_scale_metrics()


@router.get("/scale/loadbalancer")
@router.get("/api/scale/loadbalancer")
@router.get("/api/v1/scale/loadbalancer")
def get_loadbalancer() -> dict[str, Any]:
    """Retrieve NGINX multi-replica load balancer topology and distribution statistics."""
    return scale_service.get_loadbalancer_topology()


@router.post("/scale/benchmark", status_code=201)
@router.post("/api/scale/benchmark", status_code=201)
@router.post("/api/v1/scale/benchmark", status_code=201)
def trigger_benchmark(payload: BenchmarkRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Execute high-speed batch normalization benchmark and return precise throughput stats."""
    return scale_service.run_scale_benchmark(
        db,
        count=payload.count,
        target_eps=payload.target_eps,
        vendor_mix=payload.vendor_mix,
    )
