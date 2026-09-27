"""Lightweight RBAC, identity binding and maker-checker on the existing
Phase 3/5/6 governance endpoints, with every decision audited."""
import pytest
from sqlalchemy import text

from app.config import get_settings
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, PASSWORD, auth, ingest, login
from tests.integration.test_learning_flow import base_log, with_added

SAMPLES = [base_log(i) for i in range(12)]


def audit(client, **params):
    return client.get(f"{API}/governance/audit", params=params).json()["items"]


def onboarding_session(client, headers):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}, headers=headers).json()
    r = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def approve_onboarding(client, s, headers, **extra):
    return client.post(f"{API}/onboarding/sessions/{s['id']}/approve",
                       json={"proposal_version": s["proposal_version"], **extra}, headers=headers)


# --- authentication & user management -------------------------------------------------------------


def test_bootstrap_login_and_passwords_never_stored_in_plaintext(client):
    assert client.get(f"{API}/auth/status").json()["bootstrap_required"] is True
    assert client.post(f"{API}/auth/bootstrap", json={"username": "root", "password": "short"}).status_code == 400
    assert client.post(f"{API}/auth/bootstrap", json={"username": "root", "password": PASSWORD}).status_code == 201
    assert client.post(f"{API}/auth/bootstrap", json={"username": "other", "password": PASSWORD}).status_code == 409
    assert client.post(f"{API}/auth/login", json={"username": "root", "password": "wrong-password!"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"username": "nobody", "password": PASSWORD}).status_code == 401
    token = login(client, "root")
    me = client.get(f"{API}/auth/me", headers=auth(token)).json()
    assert me["role"] == "SOC_ADMIN" and me["authenticated"] and "manage_roles" in me["capabilities"]
    with TestSessionLocal() as db:
        stored = db.execute(text("SELECT password_hash FROM users WHERE username='root'")).scalar_one()
        tokens = db.execute(text("SELECT token_hash FROM auth_tokens")).scalars().all()
    assert PASSWORD not in stored and stored.startswith("pbkdf2_sha256$")
    assert token not in tokens and len(tokens) == 1
    # Nothing secret in the audit trail.
    dump = str(audit(client))
    assert PASSWORD not in dump and token not in dump
    assert {a["action"] for a in audit(client)} >= {"RBAC_BOOTSTRAP_ADMIN", "AUTH_LOGIN"}
    denied = [a for a in audit(client, action="AUTH_LOGIN") if a["decision"] == "DENIED"]
    assert len(denied) == 2


def test_invalid_and_revoked_tokens_are_rejected(client, users):
    assert client.get(f"{API}/auth/me", headers=auth("not-a-token")).status_code == 401
    t = login(client, "analyst")
    assert client.post(f"{API}/auth/logout", headers=auth(t)).status_code == 200
    assert client.get(f"{API}/auth/me", headers=auth(t)).status_code == 401
    # An invalid token on a governed action is refused (not downgraded to anonymous).
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}, headers=auth("forged")).status_code
    assert s == 401


def test_only_soc_admin_manages_roles_and_changes_are_audited(client, users):
    body = {"username": "newbie", "password": PASSWORD, "role": "ANALYST"}
    assert client.post(f"{API}/auth/users", json=body).status_code == 401  # never anonymous, even permissive
    assert client.post(f"{API}/auth/users", json=body, headers=users["eng1"]).status_code == 403
    assert client.post(f"{API}/auth/users", json=body, headers=users["admin"]).status_code == 201
    assert client.post(f"{API}/auth/users", json={**body, "role": "GOD"}, headers=users["admin"]).status_code == 422
    newbie = auth(login(client, "newbie"))
    r = client.patch(f"{API}/auth/users/newbie", json={"role": "SECURITY_ENGINEER"}, headers=users["admin"])
    assert r.status_code == 200 and r.json()["role"] == "SECURITY_ENGINEER"
    assert client.get(f"{API}/auth/me", headers=newbie).status_code == 401  # role change revokes sessions
    entry = audit(client, action="RBAC_USER_UPDATE")[0]
    assert entry["details"]["before"]["role"] == "ANALYST" and entry["details"]["after"]["role"] == "SECURITY_ENGINEER"
    assert entry["actor"] == "admin" and entry["role"] == "SOC_ADMIN"
    # The last active SOC_ADMIN can neither demote nor deactivate themselves.
    assert client.patch(f"{API}/auth/users/admin", json={"role": "ANALYST"}, headers=users["admin"]).status_code == 403
    assert client.get(f"{API}/auth/users", headers=users["analyst"]).status_code == 403


def test_config_changes_require_soc_admin_and_are_audited(client, users):
    body = {"review_sla": {"hours": {"HIGH": 12}}, "alert_thresholds": {"parser_failure_rate": 0.25}}
    assert client.put(f"{API}/governance/config", json=body).status_code == 401
    assert client.put(f"{API}/governance/config", json=body, headers=users["eng1"]).status_code == 403
    r = client.put(f"{API}/governance/config", json=body, headers=users["admin"])
    assert r.status_code == 200
    assert r.json()["review_sla"]["hours"]["HIGH"] == 12 and r.json()["alert_thresholds"]["parser_failure_rate"] == 0.25
    assert client.put(f"{API}/governance/config", json={"review_sla": {"hours": {"HIGH": -1}}},
                      headers=users["admin"]).status_code == 422
    assert client.put(f"{API}/governance/config", json={"surprise": 1}, headers=users["admin"]).status_code == 422
    change = audit(client, action="CONFIG_CHANGE")
    assert len(change) == 1 and change[0]["details"]["after"]["review_sla"]["hours"]["HIGH"] == 12


# --- RBAC on the existing endpoints --------------------------------------------------------------------


def test_analyst_can_propose_but_not_approve(client, users):
    s = onboarding_session(client, users["analyst"])
    r = approve_onboarding(client, s, users["analyst"])
    assert r.status_code == 403 and "approve_adapter" in r.json()["error"]["message"]
    assert client.post(f"{API}/onboarding/sessions/{s['id']}/reject", json={"reason": "no"},
                       headers=users["analyst"]).status_code == 403
    denied = audit(client, action="ONBOARDING_APPROVE")[0]
    assert denied["decision"] == "DENIED" and denied["actor"] == "analyst" and denied["role"] == "ANALYST"
    # nothing was activated
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "VALIDATED"


def test_identity_fields_are_bound_to_the_authenticated_user(client, users):
    s = onboarding_session(client, users["analyst"])
    r = approve_onboarding(client, s, users["eng1"], approved_by="somebody-else")
    assert r.status_code == 403 and "authenticated user" in r.json()["error"]["message"]
    r = approve_onboarding(client, s, users["eng1"], approved_by="eng1")
    assert r.status_code == 200


def test_maker_checker_blocks_self_approval_of_onboarding(client, users):
    s = onboarding_session(client, users["eng1"])  # eng1 is the maker
    r = approve_onboarding(client, s, users["eng1"])
    assert r.status_code == 403 and "Maker-checker" in r.json()["error"]["message"]
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "VALIDATED"
    r = approve_onboarding(client, s, users["eng2"])  # independent checker
    assert r.status_code == 200 and r.json()["adapter"]["version"] == 1
    entries = audit(client, action="ONBOARDING_APPROVE", object_id=s["id"])
    ok = next(e for e in entries if e["decision"] == "SUCCESS")
    assert ok["actor"] == "eng2" and ok["details"]["maker_checker"] == {
        "required": True, "makers": ["eng1"], "enforced": True, "violation": False}
    assert ok["evidence_ref"] == f"onboarding_session:{s['id']}@proposal_v{s['proposal_version']}"
    blocked = next(e for e in entries if e["decision"] == "DENIED")
    assert blocked["details"]["maker_checker"]["violation"] is True


def test_maker_checker_and_critical_approvals_for_learning(client, users):
    s = onboarding_session(client, users["analyst"])
    assert approve_onboarding(client, s, users["eng2"]).status_code == 200
    [ingest(client, base_log(100 + i)) for i in range(10)]
    first = ingest(client, with_added(1))
    assert first["status"] == "UNDER_REVIEW"
    # Accepting drift needs an engineer; replacing the baseline is a critical (SOC_ADMIN) approval.
    accept = f"{API}/events/{first['event_id']}/drift/accept"
    assert client.post(accept, json={"mode": "add_variant"}, headers=users["analyst"]).status_code == 403
    assert client.post(accept, json={"mode": "replace_baseline"}, headers=users["eng1"]).status_code == 403
    assert client.post(accept, json={"mode": "add_variant"}, headers=users["eng1"]).status_code == 200
    [ingest(client, with_added(i)) for i in range(2, 7)]
    r = client.post(f"{API}/events/{first['event_id']}/learning/propose", json={"assistant": "offline"},
                    headers=users["eng1"])
    assert r.status_code == 201, r.text
    ls = r.json()
    assert ls["decisions"][0]["by"] is None  # free-text identity left empty; the audit log names the maker
    proposed = audit(client, action="LEARNING_PROPOSE")[0]
    assert proposed["object_type"] == "learning_session" and proposed["object_id"] == ls["id"] and proposed["actor"] == "eng1"
    body = {"proposal_version": ls["proposal_version"], "activate": True}
    blocked = client.post(f"{API}/learning/sessions/{ls['id']}/approve", json=body, headers=users["eng1"])
    assert blocked.status_code == 403 and "Maker-checker" in blocked.json()["error"]["message"]
    ok = client.post(f"{API}/learning/sessions/{ls['id']}/approve", json=body, headers=users["eng2"])
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE", ok.text
    # Rollback: engineers may roll back (no maker-checker: it restores the known-good version).
    rb = client.post(f"{API}/learning/sessions/{ls['id']}/rollback", json={"reason": "test"}, headers=users["analyst"])
    assert rb.status_code == 403
    rb = client.post(f"{API}/learning/sessions/{ls['id']}/rollback", json={"reason": "test"}, headers=users["eng1"])
    assert rb.status_code == 200 and rb.json()["status"] == "ROLLED_BACK"
    actions = {(a["action"], a["decision"]) for a in audit(client, limit=200)}
    assert {("DRIFT_ADD_VARIANT", "SUCCESS"), ("DRIFT_REPLACE_BASELINE", "DENIED"), ("LEARNING_APPROVE", "DENIED"),
            ("LEARNING_APPROVE", "SUCCESS"), ("LEARNING_ROLLBACK", "SUCCESS")} <= actions


def test_enforce_mode_requires_authentication(client, users, monkeypatch):
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    r = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.post(f"{API}/demo/reset").status_code == 401
    assert client.post(f"{API}/demo/reset", headers=users["eng1"]).status_code == 403
    assert client.get(f"{API}/export/events").status_code == 401
    assert client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}, headers=users["analyst"]).status_code == 201
    # Read-only views stay available.
    assert client.get(f"{API}/views/summary").status_code == 200


def test_permissive_mode_audits_anonymous_actions_without_claiming_maker_checker(client):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    r = client.post(f"{API}/onboarding/sessions/{s['id']}/approve",
                    json={"proposal_version": s["proposal_version"], "approved_by": "free text"})
    assert r.status_code == 200
    entry = audit(client, action="ONBOARDING_APPROVE")[0]
    assert entry["actor"] == "anonymous" and entry["role"] is None and entry["authenticated"] is False
    assert entry["details"]["maker_checker"]["enforced"] is False
    assert entry["details"]["rbac_mode"] == "permissive"


def test_failed_core_actions_are_audited_as_failed(client, users):
    r = client.post(f"{API}/onboarding/sessions/01ARZ3NDEKTSV4RRFFQ69G5FAV/approve", json={"proposal_version": 1},
                    headers=users["eng1"])
    assert r.status_code == 404
    entry = audit(client, action="ONBOARDING_APPROVE")[0]
    assert entry["decision"] == "FAILED" and entry["details"]["status_code"] == 404


@pytest.mark.parametrize("path", ["/governance/policy"])
def test_policy_is_published(client, path):
    p = client.get(f"{API}{path}").json()
    critical = {r["action"] for r in p["rules"] if r["critical"]}
    assert {"ONBOARDING_APPROVE", "LEARNING_APPROVE", "LEARNING_ACTIVATE"} <= critical
    assert set(p["roles"]) == {"ANALYST", "SECURITY_ENGINEER", "SOC_ADMIN"}
