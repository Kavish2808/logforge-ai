"""FastAPI dependencies for Phase 7 endpoints: who is calling, and may they?"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.governance import roles
from app.governance.policy import actor_from_request
from app.services.auth_service import Actor


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    return actor_from_request(db, request)


def acting_as(claimed: str | None, actor: Actor) -> str:
    """Identity recorded on an approval-type decision (approve, reject, activate, rollback, review):
    the caller's free-text value when given - the governance policy has already rejected a value that
    differs from an authenticated caller - otherwise the resolved actor: the authenticated username,
    or `anonymous` (the audit log's identity for unauthenticated calls, RBAC_MODE=permissive only).
    A decision record is therefore never left without an identity."""
    if claimed is not None and claimed.strip():
        return claimed.strip()
    return actor.username


def require(capability: str, *, anonymous_in_permissive: bool = True) -> Callable[..., Actor]:
    """Capability check. Anonymous callers are accepted only in RBAC_MODE=
    permissive and only for operational capabilities; governance, role
    management and critical approvals always require an authenticated role."""
    sensitive = capability in (roles.GOVERNANCE, roles.MANAGE_ROLES, roles.CRITICAL_APPROVAL)

    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if not actor.authenticated:
            if sensitive or not anonymous_in_permissive or get_settings().rbac_mode == "enforce":
                raise HTTPException(status_code=401, detail=f"Authentication required for '{capability}'.")
            return actor
        if not roles.has_capability(actor.role, capability):
            raise HTTPException(status_code=403, detail=f"Role {actor.role} lacks capability '{capability}'.")
        return actor

    return dependency
