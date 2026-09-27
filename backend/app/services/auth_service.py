"""Local authentication and user/role management (Phase 7 RBAC).

No enterprise SSO/OAuth/FIDO2: local users with PBKDF2-hashed passwords and
opaque, hashed bearer tokens. Every user/role change is audited by the
caller (routes) through audit_service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.governance import AuditLog, AuthToken, User
from app.governance import roles, security

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
ANONYMOUS = "anonymous"
_dummy_hash: dict[int, str] = {}


def _dummy(iterations: int) -> str:
    """A hash with the *configured* cost, so a login for an unknown user costs
    the same PBKDF2 work as one for a real user (no user-enumeration timing)."""
    if iterations not in _dummy_hash:
        _dummy_hash[iterations] = security.hash_password(security.new_token(), iterations=iterations)
    return _dummy_hash[iterations]


class AuthError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Unauthorized(AuthError):
    status_code = 401


class Forbidden(AuthError):
    status_code = 403


class Conflict(AuthError):
    status_code = 409


class NotFound(AuthError):
    status_code = 404


class LockedOut(AuthError):
    status_code = 429

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


LOCKOUT_REASON = "locked_out"


def lockout_remaining(db: Session, username: str, *, now: datetime | None = None) -> int | None:
    """Seconds until `username` may try again, or None if not locked out.

    Failed logins are counted from the hash-chained audit log (AUTH_LOGIN /
    DENIED since the user's last successful login, inside the lockout
    window), so the throttle needs no extra table and cannot be reset by
    editing a counter without breaking audit verification. Attempts refused
    *because of* the lockout are audited but not counted, so a lockout ends
    at a predictable time."""
    s = get_settings()
    now = now or datetime.now(tz=timezone.utc)
    window_start = now - timedelta(minutes=s.login_lockout_minutes)
    last_success = db.execute(
        select(func.max(AuditLog.timestamp)).where(
            AuditLog.action == "AUTH_LOGIN", AuditLog.object_type == "user", AuditLog.object_id == username,
            AuditLog.decision == "SUCCESS")
    ).scalar()
    since = max(window_start, last_success) if last_success else window_start
    failures = db.execute(
        select(AuditLog.timestamp).where(
            AuditLog.action == "AUTH_LOGIN", AuditLog.object_type == "user", AuditLog.object_id == username,
            AuditLog.decision == "DENIED", AuditLog.timestamp > since,
            func.coalesce(AuditLog.details["reason"].astext, "") != LOCKOUT_REASON,
        ).order_by(AuditLog.timestamp.desc()).limit(s.login_max_failures)
    ).scalars().all()
    if len(failures) < s.login_max_failures:
        return None
    # Locked until the oldest of the last N failures leaves the window.
    until = failures[-1] + timedelta(minutes=s.login_lockout_minutes)
    remaining = int((until - now).total_seconds())
    return remaining if remaining > 0 else None


@dataclass(frozen=True)
class Actor:
    username: str
    role: str | None
    authenticated: bool
    user_id: str | None = None

    @classmethod
    def anonymous(cls) -> "Actor":
        return cls(ANONYMOUS, None, False)


def _validate_new(username: str, password: str, role: str) -> None:
    if not USERNAME_RE.match(username):
        raise AuthError("username must be 3-64 characters: lowercase letters, digits, '_', '.', '-', starting with a letter")
    if role not in roles.ROLES:
        raise AuthError(f"role must be one of {', '.join(roles.ROLES)}")
    problem = security.password_problem(password)
    if problem:
        raise AuthError(problem)


def user_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(User)).scalar_one()


def create_user(db: Session, *, username: str, password: str, role: str, created_by: str | None) -> User:
    username = username.strip().lower()
    _validate_new(username, password, role)
    if db.execute(select(User).where(User.username == username)).scalars().first() is not None:
        raise Conflict(f"user '{username}' already exists")
    user = User(id=generate_event_id(), username=username, role=role, active=True, created_by=created_by,
                password_hash=security.hash_password(password, iterations=get_settings().password_hash_iterations))
    db.add(user)
    db.flush()
    return user


def bootstrap_admin(db: Session, *, username: str, password: str) -> User:
    """Create the first SOC_ADMIN. Only possible while no user exists."""
    db.execute(select(func.pg_advisory_xact_lock(0x4C46_424F_4F54)))  # serialize concurrent bootstraps
    if user_count(db) > 0:
        raise Conflict("bootstrap is only possible while no users exist")
    return create_user(db, username=username, password=password, role=roles.SOC_ADMIN, created_by="bootstrap")


def login(db: Session, *, username: str, password: str) -> tuple[str, User, datetime]:
    remaining = lockout_remaining(db, username.strip().lower())
    if remaining is not None:
        # Checked before the password, so a correct guess during lockout is refused too.
        raise LockedOut(f"too many failed logins; try again in {remaining} seconds", remaining)
    user = db.execute(select(User).where(User.username == username.strip().lower())).scalars().first()
    # Always run one PBKDF2 verification so response time does not reveal whether the user exists.
    stored = user.password_hash if user is not None else _dummy(get_settings().password_hash_iterations)
    ok = security.verify_password(password, stored)
    if user is None or not ok or not user.active:
        raise Unauthorized("invalid username or password")
    token = security.new_token()
    expires = datetime.now(tz=timezone.utc) + timedelta(minutes=get_settings().auth_token_ttl_minutes)
    db.add(AuthToken(token_hash=security.token_digest(token), user_id=user.id, expires_at=expires, revoked=False))
    db.flush()
    return token, user, expires


def resolve(db: Session, token: str) -> Actor:
    row = db.get(AuthToken, security.token_digest(token))
    if row is None or row.revoked or row.expires_at <= datetime.now(tz=timezone.utc):
        raise Unauthorized("invalid or expired token")
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise Unauthorized("invalid or expired token")
    return Actor(user.username, user.role, True, user.id)


def logout(db: Session, token: str) -> None:
    db.execute(update(AuthToken).where(AuthToken.token_hash == security.token_digest(token)).values(revoked=True))


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.username)).scalars().all())


def get_user(db: Session, username: str) -> User:
    user = db.execute(select(User).where(User.username == username)).scalars().first()
    if user is None:
        raise NotFound(f"user '{username}' not found")
    return user


def update_user(db: Session, *, username: str, role: str | None, active: bool | None, actor: Actor) -> tuple[User, dict]:
    user = get_user(db, username)
    before = {"role": user.role, "active": user.active}
    if role is not None:
        if role not in roles.ROLES:
            raise AuthError(f"role must be one of {', '.join(roles.ROLES)}")
        user.role = role
    if active is not None:
        user.active = active
    if user.username == actor.username and (user.role != roles.SOC_ADMIN or not user.active):
        raise Forbidden("a SOC_ADMIN cannot demote or deactivate themselves")
    # Never leave the system without an active SOC_ADMIN.
    db.flush()
    admins = db.execute(select(func.count()).select_from(User).where(User.role == roles.SOC_ADMIN, User.active.is_(True))).scalar_one()
    if admins == 0:
        raise Conflict("at least one active SOC_ADMIN must remain")
    if not user.active or user.role != before["role"]:
        # Role or activation change invalidates existing sessions.
        db.execute(update(AuthToken).where(AuthToken.user_id == user.id).values(revoked=True))
    return user, before


def user_dict(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active,
            "capabilities": roles.capabilities(user.role), "created_by": user.created_by,
            "created_at": user.created_at, "updated_at": user.updated_at}
