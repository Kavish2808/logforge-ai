"""Password hashing, tokens, the role/capability matrix and SLA status math."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.governance import roles, security
from app.services import sla_service


def test_password_hash_is_salted_and_verifies():
    a = security.hash_password("correct horse battery", iterations=1000)
    b = security.hash_password("correct horse battery", iterations=1000)
    assert a != b  # per-user salt
    assert "correct horse battery" not in a and a.startswith("pbkdf2_sha256$1000$")
    assert security.verify_password("correct horse battery", a)
    assert not security.verify_password("wrong password!!", a)
    assert not security.verify_password("x", "garbage")
    assert not security.verify_password("x", "md5$1$abc$def")


def test_password_policy():
    assert security.password_problem("short") is not None
    assert security.password_problem(" leading-space-pass") is not None
    assert security.password_problem("a-long-enough-pass") is None


def test_tokens_are_random_and_stored_only_as_digest():
    t1, t2 = security.new_token(), security.new_token()
    assert t1 != t2 and len(t1) >= 40
    assert security.token_digest(t1) != t1 and len(security.token_digest(t1)) == 64


@pytest.mark.parametrize("role,cap,allowed", [
    ("ANALYST", roles.PROPOSE, True), ("ANALYST", roles.REVIEW, True), ("ANALYST", roles.APPROVE_ADAPTER, False),
    ("ANALYST", roles.APPROVE_LEARNING, False), ("ANALYST", roles.GOVERNANCE, False),
    ("SECURITY_ENGINEER", roles.APPROVE_ADAPTER, True), ("SECURITY_ENGINEER", roles.APPROVE_DRIFT, True),
    ("SECURITY_ENGINEER", roles.APPROVE_LEARNING, True), ("SECURITY_ENGINEER", roles.MANAGE_ROLES, False),
    ("SECURITY_ENGINEER", roles.CRITICAL_APPROVAL, False),
    ("SOC_ADMIN", roles.MANAGE_ROLES, True), ("SOC_ADMIN", roles.CRITICAL_APPROVAL, True), ("SOC_ADMIN", roles.PROPOSE, True),
    (None, roles.INSPECT, False), ("ROOT", roles.INSPECT, False),
])
def test_capability_matrix(role, cap, allowed):
    assert roles.has_capability(role, cap) is allowed


POLICY = {"hours": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": 168}, "due_soon_fraction": 0.75,
          "escalate_after_fraction": 0.5}


def row(hours=4.0):
    opened = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return SimpleNamespace(sla_hours=hours, opened_at=opened, due_at=opened + timedelta(hours=hours))


@pytest.mark.parametrize("elapsed_h,status,level", [
    (0, "PENDING", 0), (2.9, "PENDING", 0), (3.0, "DUE_SOON", 0), (3.99, "DUE_SOON", 0),
    (4.0, "OVERDUE", 0), (5.9, "OVERDUE", 0), (6.0, "ESCALATED", 1), (8.0, "ESCALATED", 2), (100, "ESCALATED", 10),
])
def test_sla_status_transitions(elapsed_h, status, level):
    r = row()
    assert sla_service.compute_status(r, r.opened_at + timedelta(hours=elapsed_h), POLICY) == (status, level)


def test_sla_policy_validation():
    assert sla_service.validate_policy({"hours": {"HIGH": 12}}) == {"hours": {"HIGH": 12.0}}
    for bad in ({"hours": {"URGENT": 1}}, {"hours": {"HIGH": 0}}, {"hours": {"HIGH": True}}, {"surprise": 1},
                {"due_soon_fraction": -1}):
        with pytest.raises(ValueError):
            sla_service.validate_policy(bad)


def test_next_action_never_suggests_automatic_promotion():
    for t in sla_service.ITEM_TYPES:
        for s in ("PENDING", "OVERDUE", "ESCALATED"):
            text = sla_service.next_action(t, s)
            assert "automatic" not in text.lower() and ("SECURITY_ENGINEER" in text)
