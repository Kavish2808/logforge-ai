"""Phase 7 integration fixtures: an isolated raw vault + anchor store per test,
and helpers to create users / sign in through the real API."""
import pytest

from app.alerts import channels
from app.config import get_settings
from app.evidence.anchor import LocalWormAnchorStore, set_anchor_store
from app.evidence.raw_vault import FilesystemRawVault, set_vault

API = "/api/v1"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path):
    vault = FilesystemRawVault(tmp_path / "vault")
    anchors = LocalWormAnchorStore(tmp_path / "anchors")
    set_vault(vault)
    set_anchor_store(anchors)
    channels.clear_registered_channels()
    yield {"vault": vault, "anchors": anchors, "root": tmp_path}
    set_vault(None)
    set_anchor_store(None)
    channels.clear_registered_channels()


@pytest.fixture(autouse=True)
def _phase7_defaults(monkeypatch):
    s = get_settings()
    for name, value in (("rbac_mode", "permissive"), ("anthropic_api_key", ""), ("raw_vault_enabled", True),
                        ("extension_inline_max_bytes", 8192), ("extension_inline_max_fields", 64),
                        ("overflow_evidence_min_occurrences", 3), ("merkle_seal_grace_seconds", 60),
                        ("export_batch_size", 500)):
        monkeypatch.setattr(s, name, value)


def ingest(client, raw: str) -> dict:
    r = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert r.status_code == 201, r.text
    return r.json()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client, username: str, password: str = PASSWORD) -> str:
    r = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def users(client):
    """admin (SOC_ADMIN), eng1/eng2 (SECURITY_ENGINEER), analyst (ANALYST) -> bearer headers."""
    r = client.post(f"{API}/auth/bootstrap", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 201, r.text
    admin = auth(login(client, "admin"))
    for name, role in (("eng1", "SECURITY_ENGINEER"), ("eng2", "SECURITY_ENGINEER"), ("analyst", "ANALYST")):
        r = client.post(f"{API}/auth/users", json={"username": name, "password": PASSWORD, "role": role}, headers=admin)
        assert r.status_code == 201, r.text
    return {"admin": admin, "eng1": auth(login(client, "eng1")), "eng2": auth(login(client, "eng2")),
            "analyst": auth(login(client, "analyst"))}
