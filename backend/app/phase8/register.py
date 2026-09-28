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


# Thin wrappers: the Phase 8 services are imported lazily, so importing this
# module (from app.main) never creates an import cycle with the Phase 7 core.


def _golden_guard(ctx):
    from app.services.phase8 import golden_baseline_service

    return golden_baseline_service.guard(ctx)


def _compact_lineage_hook(db, event, reprocessed):
    from app.services.phase8 import compact_lineage_service

    compact_lineage_service.persist_hook(db, event, reprocessed)


def _compact_lineage_backfill(db):
    from app.services.phase8 import compact_lineage_service

    return compact_lineage_service.backfill(db)


def _shadow_gate(ctx):
    from app.services.phase8 import shadow_service

    return shadow_service.gate(ctx)


def _revision_hook(db, event, reprocessed):
    from app.services.phase8 import revision_service

    revision_service.persist_hook(db, event, reprocessed)


def _replay_worker(db):
    from app.services.phase8 import replay_service

    return replay_service.worker_step(db)


# (name, actions, fn) / (name, fn) — filled in by the Phase 8 pillars.
GUARDS: list[tuple[str, tuple[str, ...], object]] = [
    ("golden_poisoning", ("DRIFT_ADD_VARIANT", "DRIFT_REPLACE_BASELINE", "LEARNING_ACTIVATE", "LEARNING_APPROVE"),
     _golden_guard),
    ("shadow_gate", ("LEARNING_ACTIVATE", "LEARNING_APPROVE"), _shadow_gate),
]
STEPS: list[tuple[str, object]] = [("compact_lineage_backfill", _compact_lineage_backfill),
                                   ("replay_worker", _replay_worker)]
PERSIST_HOOKS: list[tuple[str, object]] = [("compact_lineage", _compact_lineage_hook),
                                           ("event_revisions", _revision_hook)]


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
