"""Approval identity is always recorded (E2E defect D4): an authenticated approval that omits the
free-text `approved_by` used to store an empty approver on the adapter version, while the audit log
named the real actor. Decision records now carry the resolved actor and agree with the audit chain."""
import pytest

from app.config import get_settings
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API
from tests.integration.phase7.test_rbac_maker_checker import approve_onboarding, audit, onboarding_session
from tests.integration.test_learning_flow import base_log, with_added
from sqlalchemy import text


def ingest(client, raw):
    r = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert r.status_code == 201, r.text
    return r.json()


def db_approver(adapter_id, version):
    with TestSessionLocal() as db:
        return db.execute(text("SELECT approved_by FROM onboarded_adapters WHERE adapter_id=:a AND version=:v"),
                          {"a": adapter_id, "v": version}).scalar()


@pytest.fixture(autouse=True)
def _drift(monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85), ("anthropic_api_key", "")):
        monkeypatch.setattr(s, name, value)


def test_a_authenticated_approval_records_the_token_user(client, users):
    s = onboarding_session(client, users["analyst"])
    r = approve_onboarding(client, s, users["eng2"], note="reviewed")  # no approved_by in the body
    assert r.status_code == 200, r.text
    adapter = r.json()["adapter"]
    assert adapter["approved_by"] == "eng2" and adapter["approved_at"]
    assert db_approver(adapter["adapter_id"], 1) == "eng2"
    approved = [d for d in r.json()["session"]["decisions"] if d["action"] == "APPROVED"][-1]
    assert approved["by"] == "eng2"
    [entry] = [a for a in audit(client, action="ONBOARDING_APPROVE") if a["decision"] == "SUCCESS"]
    assert entry["actor"] == "eng2" and entry["authenticated"] is True and entry["object_id"] == s["id"]


def test_a2_explicit_matching_identity_is_kept_and_a_different_one_is_refused(client, users):
    s = onboarding_session(client, users["analyst"])
    assert approve_onboarding(client, s, users["eng2"], approved_by="eng1").status_code == 403
    r = approve_onboarding(client, s, users["eng2"], approved_by="eng2")
    assert r.status_code == 200 and r.json()["adapter"]["approved_by"] == "eng2"


def test_b_anonymous_permissive_approval_is_attributed_to_anonymous_not_blank(client):
    s = onboarding_session(client, {})
    r = approve_onboarding(client, s, {})
    assert r.status_code == 200, r.text
    assert r.json()["adapter"]["approved_by"] == "anonymous"
    assert db_approver(r.json()["adapter"]["adapter_id"], 1) == "anonymous"
    [entry] = [a for a in audit(client, action="ONBOARDING_APPROVE") if a["decision"] == "SUCCESS"]
    assert entry["actor"] == "anonymous" and entry["authenticated"] is False


def test_b2_enforce_mode_refuses_an_approval_without_identity(client, users, monkeypatch):
    s = onboarding_session(client, users["analyst"])
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    assert approve_onboarding(client, s, {}).status_code == 401
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM onboarded_adapters")).scalar() == 0


def test_c_maker_checker_still_blocks_the_maker_and_records_the_checker(client, users):
    s = onboarding_session(client, users["eng1"])  # eng1 is the maker
    blocked = approve_onboarding(client, s, users["eng1"])
    assert blocked.status_code == 403 and "Maker-checker" in blocked.json()["error"]["message"]
    ok = approve_onboarding(client, s, users["eng2"])
    assert ok.status_code == 200 and ok.json()["adapter"]["approved_by"] == "eng2"


def test_d_rollback_and_reject_record_the_actor(client, users):
    s1 = onboarding_session(client, users["analyst"])
    aid = approve_onboarding(client, s1, users["eng2"]).json()["adapter"]["adapter_id"]
    s2 = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i, extra_f="x") for i in range(12)]},
                     headers=users["analyst"]).json()
    s2 = client.post(f"{API}/onboarding/sessions/{s2['id']}/suggest", json={"provider": "offline"},
                     headers=users["analyst"]).json()
    rej = client.post(f"{API}/onboarding/sessions/{s2['id']}/reject", json={"reason": "not needed"}, headers=users["eng1"])
    assert rej.status_code == 200
    assert [d for d in rej.json()["decisions"] if d["action"] == "REJECTED"][-1]["by"] == "eng1"
    # a second approved version, then roll back to v1
    s3 = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i) + " zone=dmz" for i in range(12)]},
                     headers=users["analyst"]).json()
    s3 = client.post(f"{API}/onboarding/sessions/{s3['id']}/suggest", json={"provider": "offline"},
                     headers=users["analyst"]).json()
    prop = s3["proposal"]
    prop["mappings"].append({"raw_field": "zone", "target": "src_interface", "type": None, "confidence": 0.9,
                             "evidence": "reviewer"})
    s3 = client.put(f"{API}/onboarding/sessions/{s3['id']}/proposal", json={"proposal": prop}, headers=users["analyst"]).json()
    r = approve_onboarding(client, s3, users["eng2"], adapter_id=aid)
    assert r.status_code == 200 and r.json()["adapter"]["version"] == 2, r.text
    rb = client.post(f"{API}/onboarding/adapters/{aid}/rollback", json={"reason": "regression"}, headers=users["eng1"])
    assert rb.status_code == 200 and rb.json()["active_version"] == 1, rb.text
    session = client.get(f"{API}/onboarding/sessions/{s3['id']}").json()
    assert [d for d in session["decisions"] if d["action"] == "ROLLED_BACK"][-1]["by"] == "eng1"
    assert [a["actor"] for a in audit(client, action="ADAPTER_ROLLBACK") if a["decision"] == "SUCCESS"] == ["eng1"]


def test_e_learning_approval_and_activation_record_the_actor(client, users):
    s = onboarding_session(client, users["analyst"])
    assert approve_onboarding(client, s, users["eng2"]).status_code == 200
    [ingest(client, base_log(100 + i)) for i in range(10)]
    first = ingest(client, with_added(1))
    assert client.post(f"{API}/events/{first['event_id']}/drift/accept", json={"mode": "add_variant"},
                       headers=users["eng1"]).status_code == 200
    [ingest(client, with_added(i)) for i in range(2, 7)]
    ls = client.post(f"{API}/events/{first['event_id']}/learning/propose", json={"assistant": "offline"},
                     headers=users["analyst"]).json()
    assert ls["decisions"][0]["by"] is None  # maker identity lives in the audit log (unchanged Phase 7 contract)
    r = client.post(f"{API}/learning/sessions/{ls['id']}/approve", json={"proposal_version": ls["proposal_version"]},
                    headers=users["eng1"])
    assert r.status_code == 200 and r.json()["approved_by"] == "eng1", r.text
    r = client.post(f"{API}/learning/sessions/{ls['id']}/activate", json={}, headers=users["eng2"])
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    decisions = {d["action"]: d["by"] for d in r.json()["decisions"]}
    assert decisions["APPROVED"] == "eng1" and decisions["ACTIVATED"] == "eng2"
    version = r.json()["target_version"]
    assert db_approver(r.json()["source_adapter_id"], version) == "eng1"  # the learned adapter version's approver


def test_f_every_decision_identity_matches_its_audit_record(client, users):
    s = onboarding_session(client, users["analyst"])
    r = approve_onboarding(client, s, users["eng1"])
    stored = r.json()["adapter"]["approved_by"]
    [entry] = [a for a in audit(client, action="ONBOARDING_APPROVE") if a["decision"] == "SUCCESS"]
    assert stored == entry["actor"] == "eng1"
    assert client.get(f"{API}/governance/audit/verify").json()["valid"] is True
