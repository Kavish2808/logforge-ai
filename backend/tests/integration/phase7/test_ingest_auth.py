"""F-04 regression: ingestion follows RBAC_MODE like every other operational endpoint (docs/API.md).

permissive (development / Demo Mode): anonymous ingest is accepted, a presented token is enforced.
enforce (required by APP_ENV=production): every ingest route needs a valid token; anonymous and
invalid-token requests are rejected with 401 and nothing is stored. Documented open read-only views
(GET /events*) are unchanged."""
import pytest
from sqlalchemy import text

from app.config import get_settings
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, users  # noqa: F401

RAW = "<34>Sep 29 11:40:00 host1 sshd[1]: Failed password for root from 203.0.113.7 port 51522 ssh2"

ROUTES = [
    ("single", f"{API}/ingest", {"raw_log": RAW}),
    ("single-unprefixed", "/ingest", {"raw_log": RAW}),
    ("console", f"{API}/ingest", {"source": "default", "events": [RAW]}),
    ("batch", f"{API}/ingest/batch", {"logs": [{"raw_log": RAW}]}),
    ("batch-unprefixed", "/ingest/batch", {"logs": [{"raw_log": RAW}]}),
    ("demo", f"{API}/ingest/demo", None),
]


def _events() -> int:
    with TestSessionLocal() as db:
        return db.execute(text("SELECT count(*) FROM events")).scalar()


def _post(client, path, body, headers=None):
    return client.post(path, json=body, headers=headers) if body is not None else client.post(path, headers=headers)


@pytest.fixture()
def enforce(monkeypatch):
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")


@pytest.mark.parametrize("name,path,body", ROUTES, ids=[r[0] for r in ROUTES])
def test_enforce_rejects_anonymous_ingest_and_stores_nothing(client, enforce, name, path, body):
    r = _post(client, path, body)
    assert r.status_code == 401, r.text
    assert _events() == 0


@pytest.mark.parametrize("name,path,body", ROUTES, ids=[r[0] for r in ROUTES])
def test_enforce_rejects_an_invalid_token(client, enforce, name, path, body):
    r = _post(client, path, body, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401, r.text
    assert _events() == 0


@pytest.mark.parametrize("role", ["analyst", "eng1", "admin"])
def test_enforce_accepts_every_authenticated_role(client, users, enforce, role):
    for name, path, body in ROUTES:
        r = _post(client, path, body, headers=users[role])
        assert r.status_code == 201, (name, r.text)
    assert _events() > 0


def test_permissive_keeps_anonymous_ingest_for_development_and_demo(client):
    assert get_settings().rbac_mode == "permissive"
    for name, path, body in ROUTES:
        assert _post(client, path, body).status_code == 201, name


def test_permissive_still_enforces_a_presented_invalid_token(client):
    r = client.post(f"{API}/ingest", json={"raw_log": RAW}, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401 and _events() == 0


def test_enforce_keeps_documented_open_read_views(client, users, enforce):
    event = client.post(f"{API}/ingest", json={"raw_log": RAW}, headers=users["analyst"]).json()
    assert client.get(f"{API}/events").status_code == 200  # docs/API.md: GET /events* stays open
    assert client.get(f"{API}/events/{event['event_id']}").status_code == 200
    assert client.get(f"{API}/integrity/raw/{event['event_id']}/recover").status_code == 401  # needs `inspect`


def test_validation_still_runs_before_storage_for_authenticated_callers(client, users, enforce):
    r = client.post(f"{API}/ingest", json={"raw_log": ""}, headers=users["analyst"])
    assert r.status_code == 422 and _events() == 0
