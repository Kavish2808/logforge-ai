"""Export authorization per RBAC mode (E2E finding D7). Contract (README "RBAC"): export is an
ANALYST capability; RBAC_MODE=permissive accepts anonymous calls and audits them as `anonymous`;
RBAC_MODE=enforce requires a signed-in role - also for raw payloads; APP_ENV=production refuses
to start without enforce."""
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.config import Settings, get_settings
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest

RAW = '{"user":"secret-user","action":"login"}'


def export_rows():
    with TestSessionLocal() as db:
        return db.execute(text("SELECT count(*) FROM export_log")).scalar()


def test_permissive_anonymous_export_works_and_is_audited_as_anonymous(client):
    ingest(client, RAW)
    r = client.get(f"{API}/export/events", params={"include_raw": "true", "output": "json"})
    assert r.status_code == 200 and r.json()["items"][0]["raw"]["payload"] == RAW
    entry = client.get(f"{API}/governance/audit", params={"action": "EXPORT"}).json()["items"][0]
    assert entry["actor"] == "anonymous" and entry["authenticated"] is False
    assert entry["details"]["include_raw"] is True


@pytest.mark.parametrize("request_kind", ["get", "get_raw", "post", "post_raw"])
def test_enforce_mode_refuses_anonymous_export_including_raw_payloads(client, monkeypatch, request_kind):
    ingest(client, RAW)
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    raw = request_kind.endswith("_raw")
    if request_kind.startswith("get"):
        r = client.get(f"{API}/export/events", params={"include_raw": str(raw).lower()})
    else:
        r = client.post(f"{API}/export/events", json={"include_raw": raw, "output": "json"})
    assert r.status_code == 401 and "secret-user" not in r.text
    assert export_rows() == 0  # nothing was started or streamed


def test_enforce_mode_allows_an_authenticated_analyst(client, users, monkeypatch):
    ingest(client, RAW)
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    r = client.post(f"{API}/export/events", json={"include_raw": True, "output": "json"}, headers=users["analyst"])
    assert r.status_code == 200 and r.json()["items"][0]["raw"]["payload"] == RAW
    entry = client.get(f"{API}/governance/audit", params={"action": "EXPORT"}, headers=users["admin"]).json()["items"][0]
    assert entry["actor"] == "analyst" and entry["authenticated"] is True


@pytest.mark.parametrize("mode", ["permissive", "enforce"])
def test_invalid_token_is_refused_in_every_mode(client, monkeypatch, mode):
    monkeypatch.setattr(get_settings(), "rbac_mode", mode)
    r = client.get(f"{API}/export/events", headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401


def test_ndjson_export_in_enforce_mode_streams_only_for_authenticated_callers(client, users, monkeypatch):
    ingest(client, RAW)
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    r = client.get(f"{API}/export/events", params={"include_raw": "true"}, headers=users["analyst"])
    lines = [json.loads(x) for x in r.text.splitlines() if x]
    assert r.status_code == 200 and lines[-1]["record_type"] == "trailer" and lines[-1]["count"] == 1


def test_production_cannot_run_permissive():
    with pytest.raises(ValidationError):
        Settings(app_env="production", rbac_mode="permissive")
    assert Settings(app_env="production", rbac_mode="enforce").rbac_mode == "enforce"
