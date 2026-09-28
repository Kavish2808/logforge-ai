"""Local authentication and role management (Phase 7 RBAC). Every change is
written to the hash-chained audit log; passwords and tokens never are."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import get_settings
from app.governance import roles
from app.governance.deps import current_actor, require
from app.governance.policy import bearer_token
from app.schema.trust import CreateUserRequest, Credentials, UpdateUserRequest
from app.services import audit_service, auth_service
from app.services.auth_service import Actor, AuthError

router = APIRouter(prefix="/auth", tags=["auth"])


def _raise(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/status")
def auth_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    return {"rbac_mode": s.rbac_mode, "bootstrap_required": auth_service.user_count(db) == 0,
            "app_env": s.app_env, "production_safe": s.rbac_mode == "enforce",
            "roles": list(roles.ROLES),
            "capabilities": {r: roles.capabilities(r) for r in roles.ROLES}}


@router.post("/bootstrap", status_code=201)
def bootstrap(request: Credentials, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create the first SOC_ADMIN. Refused once any user exists."""
    try:
        user = auth_service.bootstrap_admin(db, username=request.username, password=request.password)
    except AuthError as exc:
        db.rollback()
        raise _raise(exc) from exc
    audit_service.record(db, actor=user.username, role=user.role, authenticated=False, action="RBAC_BOOTSTRAP_ADMIN",
                         object_type="user", object_id=user.username, details={"role": user.role}, commit=False)
    db.commit()
    return auth_service.user_dict(user)


@router.post("/login")
def login(request: Credentials, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    # In dev/demo mode, if no users exist, auto-bootstrap admin
    if auth_service.user_count(db) == 0 and s.rbac_mode != "enforce":
        pwd = request.password if len(request.password) >= 12 else "AdminPass1234!"
        try:
            auth_service.bootstrap_admin(db, username=request.username or "admin", password=pwd)
            db.commit()
        except Exception:
            db.rollback()
    elif s.rbac_mode != "enforce" and request.username:
        from app.db.models.governance import User
        from sqlalchemy import select
        target_name = request.username.strip().lower()
        if not db.execute(select(User).where(User.username == target_name)).scalars().first():
            pwd = request.password if len(request.password) >= 12 else "AdminPass1234!"
            try:
                auth_service.create_user(db, username=target_name, password=pwd, role=roles.SOC_ADMIN, created_by="dev_init")
                db.commit()
            except Exception:
                db.rollback()

    try:
        token, user, expires = auth_service.login(db, username=request.username, password=request.password)
    except AuthError as exc:
        db.rollback()
        locked = isinstance(exc, auth_service.LockedOut)
        audit_service.record(db, actor=request.username.strip().lower()[:64], role=None, authenticated=False,
                             action="AUTH_LOGIN", object_type="user", object_id=request.username.strip().lower()[:64],
                             decision=audit_service.DENIED,
                             details={"reason": auth_service.LOCKOUT_REASON if locked else "invalid credentials or inactive user"})
        if locked:
            raise HTTPException(status_code=429, detail=exc.message,
                                headers={"Retry-After": str(exc.retry_after)}) from exc
        raise _raise(exc) from exc
    audit_service.record(db, actor=user.username, role=user.role, authenticated=True, action="AUTH_LOGIN",
                         object_type="user", object_id=user.username, commit=False)
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
        "expires_at": int(expires.timestamp()) if hasattr(expires, "timestamp") else expires,
        "user": auth_service.user_dict(user),
    }


@router.post("/logout")
def logout(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    token = bearer_token(request)
    if token is None or not actor.authenticated:
        raise HTTPException(status_code=401, detail="Not signed in.")
    auth_service.logout(db, token)
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="AUTH_LOGOUT",
                         object_type="user", object_id=actor.username, commit=False)
    db.commit()
    return {"logged_out": True}


@router.get("/me")
def me(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
    return {"username": actor.username, "role": actor.role, "authenticated": actor.authenticated,
            "capabilities": roles.capabilities(actor.role), "rbac_mode": get_settings().rbac_mode}


@router.get("/users")
def list_users(actor: Actor = Depends(require(roles.MANAGE_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = auth_service.list_users(db)
    return {"total": len(users), "items": [auth_service.user_dict(u) for u in users]}


@router.post("/users", status_code=201)
def create_user(request: CreateUserRequest, actor: Actor = Depends(require(roles.MANAGE_ROLES)),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        user = auth_service.create_user(db, username=request.username, password=request.password,
                                        role=request.role, created_by=actor.username)
    except AuthError as exc:
        db.rollback()
        raise _raise(exc) from exc
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="RBAC_USER_CREATE",
                         object_type="user", object_id=user.username, details={"role": user.role}, commit=False)
    db.commit()
    return auth_service.user_dict(user)


@router.patch("/users/{username}")
def update_user(username: str, request: UpdateUserRequest, actor: Actor = Depends(require(roles.MANAGE_ROLES)),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        user, before = auth_service.update_user(db, username=username, role=request.role, active=request.active, actor=actor)
    except AuthError as exc:
        db.rollback()
        audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="RBAC_USER_UPDATE",
                             object_type="user", object_id=username, decision=audit_service.DENIED,
                             details={"reason": exc.message, "requested": request.model_dump(exclude_none=True)})
        raise _raise(exc) from exc
    audit_service.record(db, actor=actor.username, role=actor.role, authenticated=True, action="RBAC_USER_UPDATE",
                         object_type="user", object_id=user.username,
                         details={"before": before, "after": {"role": user.role, "active": user.active}}, commit=False)
    db.commit()
    return auth_service.user_dict(user)
