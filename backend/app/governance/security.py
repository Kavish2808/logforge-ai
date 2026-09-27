"""Password hashing and bearer tokens (standard library only).

Passwords: PBKDF2-HMAC-SHA256 with a per-user 16-byte random salt and a
configurable iteration count, stored as
    pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>
and compared in constant time. Plaintext passwords are never stored or logged.

Tokens: 256-bit random URL-safe strings; only their SHA-256 is stored, so a
database leak does not reveal usable tokens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


def hash_password(password: str, *, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"password must be at most {MAX_PASSWORD_LENGTH} characters"
    if password.strip() != password or not password.strip():
        return "password must not start or end with whitespace"
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
