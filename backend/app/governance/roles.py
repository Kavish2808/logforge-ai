"""Roles and capabilities.

Roles are hierarchical: every capability of a lower role is also held by
the roles above it.

    ANALYST            inspect, review, propose
    SECURITY_ENGINEER  + approve/reject adapters, approve/reject drift,
                         approve/reject/activate learning, rollback
    SOC_ADMIN          + governance/configuration, role management,
                         critical approvals (HIGH-risk / supersede learning,
                         baseline replacement), demo reset
"""
from __future__ import annotations

ANALYST = "ANALYST"
SECURITY_ENGINEER = "SECURITY_ENGINEER"
SOC_ADMIN = "SOC_ADMIN"
ROLES = (ANALYST, SECURITY_ENGINEER, SOC_ADMIN)
_RANK = {ANALYST: 1, SECURITY_ENGINEER: 2, SOC_ADMIN: 3}

INSPECT = "inspect"
REVIEW = "review"
PROPOSE = "propose"
APPROVE_ADAPTER = "approve_adapter"
APPROVE_DRIFT = "approve_drift"
APPROVE_LEARNING = "approve_learning"
ROLLBACK = "rollback"
EXPORT = "export"
GOVERNANCE = "governance"
MANAGE_ROLES = "manage_roles"
CRITICAL_APPROVAL = "critical_approval"

CAPABILITY_MIN_ROLE: dict[str, str] = {
    INSPECT: ANALYST,
    REVIEW: ANALYST,
    PROPOSE: ANALYST,
    EXPORT: ANALYST,
    APPROVE_ADAPTER: SECURITY_ENGINEER,
    APPROVE_DRIFT: SECURITY_ENGINEER,
    APPROVE_LEARNING: SECURITY_ENGINEER,
    ROLLBACK: SECURITY_ENGINEER,
    GOVERNANCE: SOC_ADMIN,
    MANAGE_ROLES: SOC_ADMIN,
    CRITICAL_APPROVAL: SOC_ADMIN,
}


def has_capability(role: str | None, capability: str) -> bool:
    if role not in _RANK:
        return False
    return _RANK[role] >= _RANK[CAPABILITY_MIN_ROLE[capability]]


def capabilities(role: str | None) -> list[str]:
    return [c for c in CAPABILITY_MIN_ROLE if has_capability(role, c)]
