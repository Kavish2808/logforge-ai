"""Governance policy for the existing (Phase 3/5/6) mutating endpoints.

Attached as a router-level dependency in app.main, so the frozen Phase 3/5/6
routers and services are not modified. For every governed request it:

1. resolves the caller (bearer token -> user/role; otherwise anonymous);
2. applies RBAC — in RBAC_MODE=enforce a token is required; in the default
   "permissive" mode anonymous calls still work (audited as `anonymous`),
   but any presented token is fully enforced;
3. binds free-text identity fields (approved_by, rejected_by, requested_by,
   by, activated_by, submitted_by) to the authenticated user;
4. enforces maker-checker on critical actions: an authenticated actor who
   proposed/edited the object cannot be its approver/activator;
5. writes a hash-chained audit record of the outcome (SUCCESS / DENIED /
   FAILED) with actor, role, action, object, timestamp, decision, evidence.

Maker-checker can only be enforced between *identified* actors. With
anonymous proposals (permissive mode) the audit record says so explicitly.

Phase 8 hook (additive): pre-action guards registered with `register_guard`
run after the checks above and before the action. A guard may BLOCK (409) or
require ELEVATED review (authenticated SOC_ADMIN + a written `note`); both are
audited with the guard's reason and evidence. A guard that crashes blocks the
action (fail closed). With no guards registered behavior is unchanged.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.db.models.learning import LearningSession
from app.governance import roles
from app.services import audit_service
from app.services.auth_service import Actor, AuthError, resolve

logger = logging.getLogger(__name__)

IDENTITY_FIELDS = ("approved_by", "rejected_by", "requested_by", "by", "activated_by", "submitted_by")


@dataclass(frozen=True)
class Rule:
    action: str
    object_type: str
    object_param: str | None
    capability: Callable[[Session, dict[str, Any], dict[str, str]], str]
    critical: bool = False
    maker_actions: tuple[str, ...] = ()  # proposing actions whose actors may not approve (maker-checker)
    identity_fields: tuple[str, ...] = field(default_factory=lambda: IDENTITY_FIELDS)
    action_for: Callable[[dict[str, Any]], str] | None = None


@dataclass(frozen=True)
class GuardContext:
    db: Session
    action: str
    object_type: str
    object_id: str | None
    params: dict[str, str]
    body: dict[str, Any]
    actor: Actor


@dataclass(frozen=True)
class GuardDecision:
    allow: bool = True
    elevated: bool = False  # allowed only for an authenticated SOC_ADMIN with a written note
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


_GUARDS: dict[str, list[tuple[str, Callable[[GuardContext], GuardDecision | None]]]] = {}


def register_guard(name: str, actions: tuple[str, ...], fn: Callable[[GuardContext], GuardDecision | None]) -> None:
    """Register a pre-action guard for the given audit action names (idempotent per name)."""
    for action in actions:
        entries = _GUARDS.setdefault(action, [])
        entries[:] = [e for e in entries if e[0] != name] + [(name, fn)]


def unregister_guard(name: str) -> None:
    for entries in _GUARDS.values():
        entries[:] = [e for e in entries if e[0] != name]


def _static(cap: str) -> Callable[..., str]:
    return lambda db, body, params: cap


def _drift_capability(db, body, params) -> str:
    return {"acknowledge": roles.REVIEW, "add_variant": roles.APPROVE_DRIFT}.get(
        str(body.get("mode")), roles.CRITICAL_APPROVAL)  # replace_baseline (and anything unknown)


def _learning_approve_capability(db, body, params) -> str:
    session = db.get(LearningSession, params.get("session_id"))
    if body.get("confirm_supersede") or (session is not None and session.risk == "HIGH"):
        return roles.CRITICAL_APPROVAL
    return roles.APPROVE_LEARNING


ONBOARDING_MAKERS = ("ONBOARDING_SUGGEST", "ONBOARDING_PROPOSAL_SUBMIT")
LEARNING_MAKERS = ("LEARNING_PROPOSE", "LEARNING_PROPOSAL_SUBMIT")

RULES: dict[tuple[str, str], Rule] = {
    ("POST", "/onboarding/sessions"): Rule("ONBOARDING_SESSION_CREATE", "onboarding_session", None, _static(roles.PROPOSE)),
    ("POST", "/onboarding/sessions/{session_id}/suggest"): Rule(
        "ONBOARDING_SUGGEST", "onboarding_session", "session_id", _static(roles.PROPOSE)),
    ("PUT", "/onboarding/sessions/{session_id}/proposal"): Rule(
        "ONBOARDING_PROPOSAL_SUBMIT", "onboarding_session", "session_id", _static(roles.PROPOSE)),
    ("POST", "/onboarding/sessions/{session_id}/approve"): Rule(
        "ONBOARDING_APPROVE", "onboarding_session", "session_id", _static(roles.APPROVE_ADAPTER),
        critical=True, maker_actions=ONBOARDING_MAKERS),
    ("POST", "/onboarding/sessions/{session_id}/reject"): Rule(
        "ONBOARDING_REJECT", "onboarding_session", "session_id", _static(roles.APPROVE_ADAPTER)),
    ("POST", "/onboarding/adapters/{adapter_id}/rollback"): Rule(
        "ADAPTER_ROLLBACK", "adapter", "adapter_id", _static(roles.ROLLBACK)),
    ("POST", "/events/{event_id}/drift/accept"): Rule(
        "DRIFT_DECISION", "event", "event_id", _drift_capability,
        action_for=lambda body: {"acknowledge": "DRIFT_ACKNOWLEDGE", "add_variant": "DRIFT_ADD_VARIANT",
                                 "replace_baseline": "DRIFT_REPLACE_BASELINE"}.get(str(body.get("mode")), "DRIFT_DECISION")),
    ("POST", "/events/{event_id}/reprocess"): Rule("EVENT_REPROCESS", "event", "event_id", _static(roles.REVIEW)),
    ("POST", "/events/{event_id}/learning/propose"): Rule(
        "LEARNING_PROPOSE", "learning_session", "event_id", _static(roles.PROPOSE)),
    ("PUT", "/learning/sessions/{session_id}/proposal"): Rule(
        "LEARNING_PROPOSAL_SUBMIT", "learning_session", "session_id", _static(roles.PROPOSE)),
    ("POST", "/learning/sessions/{session_id}/validate"): Rule(
        "LEARNING_REVALIDATE", "learning_session", "session_id", _static(roles.PROPOSE)),
    ("POST", "/learning/sessions/{session_id}/request-review"): Rule(
        "LEARNING_REQUEST_REVIEW", "learning_session", "session_id", _static(roles.REVIEW)),
    ("POST", "/learning/sessions/{session_id}/approve"): Rule(
        "LEARNING_APPROVE", "learning_session", "session_id", _learning_approve_capability,
        critical=True, maker_actions=LEARNING_MAKERS),
    ("POST", "/learning/sessions/{session_id}/reject"): Rule(
        "LEARNING_REJECT", "learning_session", "session_id", _static(roles.APPROVE_LEARNING)),
    ("POST", "/learning/sessions/{session_id}/activate"): Rule(
        "LEARNING_ACTIVATE", "learning_session", "session_id", _static(roles.APPROVE_LEARNING),
        critical=True, maker_actions=LEARNING_MAKERS),
    ("POST", "/learning/sessions/{session_id}/rollback"): Rule(
        "LEARNING_ROLLBACK", "learning_session", "session_id", _static(roles.ROLLBACK)),
    ("POST", "/demo/reset"): Rule("DEMO_RESET", "demo", None, _static(roles.GOVERNANCE)),
}


def match(request: Request) -> Rule | None:
    route = request.scope.get("route")
    if route is None:
        return None
    path = getattr(route, "path", "")
    prefix = get_settings().api_v1_prefix
    if path.startswith(prefix):
        path = path[len(prefix):]
    return RULES.get((request.method, path))


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def actor_from_request(db: Session, request: Request) -> Actor:
    token = bearer_token(request)
    if token is None:
        return Actor.anonymous()
    try:
        return resolve(db, token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc


def _summarize_body(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in body.items():
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:500]
        elif isinstance(value, dict):
            out[key] = {"keys": sorted(value)[:50]}
        elif isinstance(value, list):
            out[key] = {"items": len(value)}
    return out


def learning_makers(db: Session, session_id: str) -> set[str]:
    makers = audit_service.makers(db, "learning_session", session_id, LEARNING_MAKERS)
    session = db.get(LearningSession, session_id)
    if session is not None:
        for d in session.decisions or []:
            if d.get("action") in ("PROPOSED", "PROPOSAL_SUBMITTED") and d.get("by"):
                makers.add(str(d["by"]))
    return makers


def makers_for(db: Session, rule: Rule, object_id: str) -> set[str]:
    if rule.object_type == "learning_session":
        return learning_makers(db, object_id)
    return audit_service.makers(db, rule.object_type, object_id, rule.maker_actions)


def _audit(db: Session, actor: Actor, action: str, rule: Rule, object_id: str | None, decision: str,
           details: dict[str, Any], evidence_ref: str | None = None) -> None:
    try:
        audit_service.record(db, actor=actor.username, role=actor.role, authenticated=actor.authenticated,
                             action=action, object_type=rule.object_type, object_id=object_id,
                             decision=decision, details=details, evidence_ref=evidence_ref)
    except Exception:  # noqa: BLE001 — an audit write failure is logged loudly; never masks the real outcome
        db.rollback()
        logger.exception("AUDIT WRITE FAILED for %s on %s/%s", action, rule.object_type, object_id)


async def governed(request: Request, db: Session = Depends(get_db)) -> AsyncGenerator[Actor, None]:
    rule = match(request)
    if rule is None:
        yield Actor.anonymous()
        return
    settings = get_settings()
    params = dict(request.path_params)
    try:
        body = await request.json() if (await request.body()) else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    action = rule.action_for(body) if rule.action_for else rule.action
    object_id = params.get(rule.object_param) if rule.object_param else None
    details: dict[str, Any] = {"method": request.method, "path_params": params, "request": _summarize_body(body),
                               "rbac_mode": settings.rbac_mode}

    try:
        actor = actor_from_request(db, request)
    except HTTPException as exc:
        _audit(db, Actor("invalid-token", None, False), action, rule, object_id, audit_service.DENIED,
               {**details, "reason": exc.detail})
        raise

    def deny(status: int, reason: str) -> HTTPException:
        _audit(db, actor, action, rule, object_id, audit_service.DENIED, {**details, "reason": reason})
        return HTTPException(status_code=status, detail=reason)

    if not actor.authenticated and settings.rbac_mode == "enforce":
        raise deny(401, "Authentication required (RBAC_MODE=enforce).")

    capability = rule.capability(db, body, params)
    details["capability"] = capability
    if actor.authenticated:
        if not roles.has_capability(actor.role, capability):
            raise deny(403, f"Role {actor.role} lacks capability '{capability}' required for {action}.")
        for f in rule.identity_fields:
            claimed = body.get(f)
            if claimed not in (None, "") and str(claimed) != actor.username:
                raise deny(403, f"'{f}' must be the authenticated user ('{actor.username}'), not '{claimed}'.")

    if rule.critical and object_id:
        makers = makers_for(db, rule, object_id)
        mc: dict[str, Any] = {"required": True, "makers": sorted(makers)}
        if actor.authenticated and actor.username in makers:
            details["maker_checker"] = {**mc, "enforced": True, "violation": True}
            raise deny(403, f"Maker-checker: '{actor.username}' proposed or edited this {rule.object_type} "
                            "and cannot also approve/activate it.")
        if not actor.authenticated:
            mc.update(enforced=False, note="approver is anonymous (RBAC_MODE=permissive); maker-checker not enforceable")
        elif not makers:
            mc.update(enforced=True, note="no identified maker recorded (proposal was anonymous or system-generated)")
        else:
            mc.update(enforced=True, violation=False)
        details["maker_checker"] = mc

    guard_results = _run_guards(GuardContext(db, action, rule.object_type, object_id, params, body, actor))
    if guard_results:
        details["guards"] = guard_results
        blocked = next((g for g in guard_results if g["verdict"] == "BLOCK"), None)
        if blocked is not None:
            raise deny(409, f"Phase 8 guard '{blocked['guard']}' blocked {action}: {blocked['reason']}")
        elevated = [g for g in guard_results if g["verdict"] == "ELEVATED"]
        if elevated:
            note = str(body.get("note") or "").strip()
            if not (actor.authenticated and actor.role == roles.SOC_ADMIN and note):
                raise deny(403, f"Elevated review required by Phase 8 guard '{elevated[0]['guard']}': "
                                f"{elevated[0]['reason']} An authenticated SOC_ADMIN must approve with a written note.")

    try:
        yield actor
    except HTTPException as exc:
        db.rollback()
        _audit(db, actor, action, rule, object_id, audit_service.FAILED,
               {**details, "status_code": exc.status_code, "error": str(exc.detail)[:500]})
        raise
    except Exception as exc:
        db.rollback()
        _audit(db, actor, action, rule, object_id, audit_service.FAILED, {**details, "error": type(exc).__name__})
        raise
    else:
        db.rollback()  # discard any stale state; the service already committed its change
        object_id = _after_success(db, rule, action, object_id, params, body, details)
        evidence = f"{rule.object_type}:{object_id}" if object_id else None
        if "proposal_version" in body:
            evidence = f"{evidence}@proposal_v{body['proposal_version']}"
        _audit(db, actor, action, rule, object_id, audit_service.SUCCESS, details, evidence)


def _run_guards(ctx: GuardContext) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, fn in list(_GUARDS.get(ctx.action, [])):
        try:
            decision = fn(ctx)
        except Exception as exc:  # noqa: BLE001 — a broken guard must never let an unsafe action through
            ctx.db.rollback()
            logger.exception("Phase 8 guard %s crashed on %s", name, ctx.action)
            results.append({"guard": name, "verdict": "BLOCK", "reason": f"guard error ({type(exc).__name__}); failing closed",
                            "evidence": {}})
            continue
        if decision is None:
            continue
        verdict = "BLOCK" if not decision.allow else "ELEVATED" if decision.elevated else "ALLOW"
        results.append({"guard": name, "verdict": verdict, "reason": decision.reason, "evidence": decision.evidence})
    return results


def _after_success(db: Session, rule: Rule, action: str, object_id: str | None, params: dict[str, str],
                   body: dict[str, Any], details: dict[str, Any]) -> str | None:
    """Resolve the governed object where the path names something else, and
    record confidence evidence for new suggestions. Never raises."""
    from app.services import confidence_service  # local import: avoids an import cycle at startup

    try:
        if action == "LEARNING_PROPOSE":
            session = db.execute(
                select(LearningSession).where(LearningSession.trigger_event_id == params.get("event_id"))
                .order_by(LearningSession.created_at.desc()).limit(1)
            ).scalars().first()
            if session is not None:
                details["trigger_event_id"] = params.get("event_id")
                object_id = session.id
        if action in ("ONBOARDING_SUGGEST", "ONBOARDING_PROPOSAL_SUBMIT") and object_id:
            confidence_service.record_onboarding(db, object_id)
        if action in ("LEARNING_PROPOSE", "LEARNING_PROPOSAL_SUBMIT") and object_id:
            confidence_service.record_learning(db, object_id)
    except Exception:  # noqa: BLE001 — post-success bookkeeping must never fail a committed action
        db.rollback()
        logger.exception("Post-action bookkeeping failed for %s %s", action, object_id)
    return object_id
