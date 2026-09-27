"""Single wiring point between Phase 8 and the approved Phase 7 hooks.

Phase 8 plugs into the system only through three registries:
- governance.policy.register_guard        (pre-action guards: block / elevated review)
- services.scheduler.register_step        (background steps after the Phase 7 steps)
- services.evidence_service.register_persist_hook  (per-event callbacks in the ingest transaction)

`register_all()` is idempotent. With PHASE8_ENABLED=false nothing is
registered and the system behaves exactly as Phase 7.
"""
from __future__ import annotations

from app.config import get_settings

# (name, actions, fn) / (name, fn) — filled in by the Phase 8 pillars.
GUARDS: list[tuple[str, tuple[str, ...], object]] = []
STEPS: list[tuple[str, object]] = []
PERSIST_HOOKS: list[tuple[str, object]] = []


def register_all() -> dict[str, list[str]]:
    from app.governance import policy
    from app.services import evidence_service, scheduler

    unregister_all()
    if not get_settings().phase8_enabled:
        return {"guards": [], "steps": [], "persist_hooks": []}
    for name, actions, fn in GUARDS:
        policy.register_guard(name, actions, fn)
    for name, fn in STEPS:
        scheduler.register_step(name, fn)
    for name, fn in PERSIST_HOOKS:
        evidence_service.register_persist_hook(name, fn)
    return {"guards": [g[0] for g in GUARDS], "steps": [s[0] for s in STEPS],
            "persist_hooks": [h[0] for h in PERSIST_HOOKS]}


def unregister_all() -> None:
    from app.governance import policy
    from app.services import evidence_service, scheduler

    for name, _, _ in GUARDS:
        policy.unregister_guard(name)
    for name, _ in STEPS:
        scheduler.unregister_step(name)
    for name, _ in PERSIST_HOOKS:
        evidence_service.unregister_persist_hook(name)
