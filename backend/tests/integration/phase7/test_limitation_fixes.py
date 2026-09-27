"""Phase 7 hardening: production RBAC gate, failed-login throttling, and
provider-pluggable anchors / raw vault (verification resolves the backend each
batch/object was written to; unknown providers are reported, never faked)."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.config import Settings, get_settings
from app.evidence import anchor, raw_vault
from app.evidence.anchor import AnchorError, AnchorStore, LocalWormAnchorStore
from app.evidence.raw_vault import FilesystemRawVault, RawVault, VaultError
from app.services import auth_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, PASSWORD, ingest

# --- production RBAC gate ------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "PROD", " Production "])
def test_production_refuses_permissive_rbac(env):
    with pytest.raises(ValidationError, match="RBAC_MODE=enforce"):
        Settings(app_env=env, rbac_mode="permissive", _env_file=None)
    assert Settings(app_env=env, rbac_mode="enforce", _env_file=None).rbac_mode == "enforce"


def test_development_and_demo_keep_permissive():
    assert Settings(app_env="development", rbac_mode="permissive", _env_file=None).is_production is False


def test_auth_status_reports_production_safety(client, monkeypatch):
    body = client.get(f"{API}/auth/status").json()
    assert body["production_safe"] is False and body["rbac_mode"] == "permissive"
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    assert client.get(f"{API}/auth/status").json()["production_safe"] is True


# --- failed-login throttling -----------------------------------------------------------------------


def login(client, password):
    return client.post(f"{API}/auth/login", json={"username": "admin", "password": password})


@pytest.fixture()
def admin(client):
    assert client.post(f"{API}/auth/bootstrap", json={"username": "admin", "password": PASSWORD}).status_code == 201


def test_lockout_after_repeated_failures_even_with_the_right_password(client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    for _ in range(3):
        assert login(client, "wrong-password-1").status_code == 401
    r = login(client, PASSWORD)
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert 0 < int(r.headers["Retry-After"]) <= 15 * 60
    denied = client.get(f"{API}/governance/audit", params={"action": "AUTH_LOGIN"}).json()["items"]
    assert denied[0]["details"]["reason"] == "locked_out"
    # Lockout denials are not counted, so the lockout ends when the window passes.
    with TestSessionLocal() as db:
        later = datetime.now(tz=timezone.utc) + timedelta(minutes=16)
        assert auth_service.lockout_remaining(db, "admin", now=later) is None
    # The throttle lives in the audit log, whose chain still verifies.
    assert client.get(f"{API}/governance/audit/verify").json()["valid"] is True


def test_success_resets_the_failure_count(client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    for _ in range(2):
        login(client, "wrong-password-1")
    assert login(client, PASSWORD).status_code == 200
    for _ in range(2):
        login(client, "wrong-password-1")
    assert login(client, PASSWORD).status_code == 200


def test_unknown_usernames_are_throttled_too(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures", 2)
    for _ in range(2):
        assert client.post(f"{API}/auth/login", json={"username": "ghost", "password": "whatever-123"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"username": "ghost", "password": "whatever-123"}).status_code == 429


# --- provider-pluggable anchors and vault ------------------------------------------------------------


def test_verification_uses_the_backend_each_batch_was_anchored_to(client):
    for i in range(3):
        ingest(client, f'{{"user":"u{i}","action":"x"}}')
    client.post(f"{API}/integrity/seal", json={"force": True})
    assert client.get(f"{API}/integrity/verify").json()["valid"]
    with TestSessionLocal() as db:
        db.execute(text("UPDATE evidence_batches SET anchor_backend='objectlock_future' WHERE seq=1"))
        db.commit()
    chain = client.get(f"{API}/integrity/verify").json()
    assert [p["problem"] for p in chain["problems"]] == ["ANCHOR_BACKEND_UNAVAILABLE"]  # not a misleading ANCHOR_MISSING
    assert chain["anchor_store"]["compliance_grade"] is False
    e = client.get(f"{API}/integrity/batches").json()["items"][0]
    assert e["anchor_backend"] == "objectlock_future"


def test_recovery_uses_the_backend_each_object_was_written_to(client):
    e = ingest(client, "backend pinned raw")
    with TestSessionLocal() as db:
        db.execute(text("UPDATE event_raw_storage SET backend='s3_future' WHERE event_id=:i"), {"i": e["event_id"]})
        db.commit()
    rec = client.get(f"{API}/integrity/raw/{e['event_id']}/recover").json()
    assert rec["recovered"] is False and "s3_future" in rec["reason"]
    v = client.get(f"{API}/integrity/events/{e['event_id']}").json()
    assert v["cold_copy"]["status"] == "BACKEND_UNAVAILABLE" and v["cold_copy"]["valid"] is None


def test_unknown_configured_backends_fail_loudly(monkeypatch, tmp_path):
    anchor.set_anchor_store(None)
    raw_vault.set_vault(None)
    s = get_settings()
    monkeypatch.setattr(s, "evidence_anchor_backend", "blockchain")
    monkeypatch.setattr(s, "raw_vault_backend", "s3")
    with pytest.raises(AnchorError, match="not available"):
        anchor.get_anchor_store()
    with pytest.raises(VaultError, match="not available"):
        raw_vault.get_vault()


# --- provider contract suites (any future backend must pass these) ------------------------------------


def anchor_contract(store: AnchorStore):
    rec = {"seq": 1, "batch_id": "B1", "root_hash": "a" * 64, "prev_chain_hash": "0" * 64, "chain_hash": "c" * 64,
           "event_count": 1, "start_time": "t0", "end_time": "t1"}
    store.append(rec)
    assert store.get(1) == rec and store.get(2) is None
    store.append(rec)  # identical re-append is idempotent
    with pytest.raises(AnchorError):
        store.append({**rec, "root_hash": "b" * 64})  # never overwritten
    assert store.get(1) == rec
    assert store.describe()["backend"] == store.backend and store.describe()["compliance_grade"] is store.compliance_grade


def vault_contract(vault: RawVault):
    data = "exact ✓ bytes\r\n".encode()
    obj = vault.put(data)
    assert obj.backend == vault.backend and vault.get(obj.key) == data and vault.exists(obj.key)
    assert vault.put(data) == obj  # content-addressed, idempotent
    assert vault.verify(obj.key, obj.sha256)


@pytest.mark.parametrize("name", sorted(anchor.PROVIDERS))
def test_every_registered_anchor_provider_meets_the_contract(name, tmp_path, monkeypatch):
    anchor.set_anchor_store(None)
    monkeypatch.setattr(get_settings(), "evidence_anchor_path", str(tmp_path))
    store = anchor.get_anchor_store(name)
    assert isinstance(store, AnchorStore)
    anchor_contract(store)


@pytest.mark.parametrize("name", sorted(raw_vault.BACKENDS))
def test_every_registered_vault_backend_meets_the_contract(name, tmp_path, monkeypatch):
    raw_vault.set_vault(None)
    monkeypatch.setattr(get_settings(), "raw_vault_path", str(tmp_path))
    vault_contract(raw_vault.get_vault(name))


def test_local_providers_do_not_claim_compliance_grade(tmp_path):
    assert LocalWormAnchorStore(tmp_path).compliance_grade is False
    assert isinstance(FilesystemRawVault(tmp_path), RawVault)
