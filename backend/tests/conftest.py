"""Shared pytest fixtures.

Unit tests (tests/unit/**) exercise pure functions/classes and never touch
the database. Integration tests (tests/integration/**) exercise the full
FastAPI app against a real, but *dedicated*, Postgres database.

Tests run against `<configured db name>_test`, never the database the live
docker-compose `backend`/`frontend` stack uses. This matters: the `client`/
`db_session` fixtures delete all rows from `events` between tests, and
`docker compose run backend pytest` shares the same Postgres server (and,
without this isolation, the same database) as a stack a developer may
already have running with real demo data in it — running the suite must
never be able to wipe that.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.config import get_settings
from app.db.base import Base
from app.main import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _test_database_url() -> str:
    base_url, _, db_name = get_settings().database_url.rpartition("/")
    return f"{base_url}/{db_name}_test"


def _ensure_test_database_exists() -> None:
    admin_url = get_settings().database_url  # connect using the app's own (existing) database
    db_name = _test_database_url().rpartition("/")[-1]
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()


_ensure_test_database_exists()
test_engine = create_engine(_test_database_url(), pool_pre_ping=True, future=True)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)


def _override_get_db():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture()
def db_session():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.execute(text("DELETE FROM events"))
        # Phase 5: drift baselines are per-source state that would otherwise
        # leak between tests (a later test drifting against an earlier one's).
        session.execute(text("DELETE FROM source_baselines"))
        session.execute(text("DELETE FROM source_baseline_history"))
        # Phase 6: learning sessions reference onboarded_adapters (FK) -> delete first.
        session.execute(text("DELETE FROM learning_sessions"))
        session.execute(text("DELETE FROM onboarded_adapters"))
        session.execute(text("DELETE FROM onboarding_sessions"))
        session.commit()
        session.close()


@pytest.fixture()
def client(db_session):
    with TestClient(app) as test_client:
        yield test_client


def load_fixture(relative_path: str) -> str:
    return (FIXTURES_DIR / relative_path).read_text(encoding="utf-8").strip("\n")
