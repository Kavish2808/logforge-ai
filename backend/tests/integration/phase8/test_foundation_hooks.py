"""Phase 8 foundation: the three additive Phase 7 hooks (guards, scheduler
steps, persist hooks) and the PHASE8_ENABLED switch. Each hook must be a
no-op when nothing is registered, and must never weaken existing behavior."""
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.governance import policy
from app.governance.policy import GuardDecision
from app.phase8 import register as p8
from app.services import evidence_service, scheduler
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import (  # noqa: F401 — fixtures reused
    API,
    _phase7_defaults,
    auth,
    ingest,
    isolated_stores,
    users,
)
from tests.integration.test_learning_flow import base_log

SAMPLES = [base_log(i) for i in range(12)]
GUARD = "test_guard"


@pytest.fixture(autouse=True)
def _clean_registries():
    yield
    policy.unregister_guard(GUARD)
    scheduler.unregister_step("test_step")
    scheduler.unregister_step("test_failing_step")
    evidence_service.unregister_persist_hook("test_hook")
    evidence_service.unregister_persist_hook("test_failing_hook")
    p8.register_all()


def validated_session(client, headers=None):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": SAMPLES}, headers=headers).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}, headers=headers).json()
    assert s["validation"]["result"] == "PASSED"
    return s


def approve(client, s, headers=None, **extra):
    return client.post(f"{API}/onboarding/sessions/{s['id']}/approve",
                       json={"proposal_version": s["proposal_version"], **extra}, headers=headers)


def last_audit(client, action):
    return client.get(f"{API}/governance/audit", params={"action": action}).json()["items"][0]


# --- H1: pre-action guards ----------------------------------------------------------------------------


def test_no_guard_registered_means_identical_behavior(client):
    assert not policy._GUARDS.get("ONBOARDING_APPROVE")
    s = validated_session(client)
    assert approve(client, s).status_code == 200
    entry = last_audit(client, "ONBOARDING_APPROVE")
    assert entry["decision"] == "SUCCESS" and "guards" not in entry["details"]
    assert set(entry["details"]) == {"method", "path_params", "request", "rbac_mode", "capability", "maker_checker"}


def test_blocking_guard_stops_the_action_and_is_audited(client):
    seen = {}

    def guard(ctx):
        seen.update(action=ctx.action, object_type=ctx.object_type, object_id=ctx.object_id, actor=ctx.actor.username)
        return GuardDecision(allow=False, reason="unsafe change", evidence={"score": 0.1})

    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), guard)
    s = validated_session(client)
    r = approve(client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    assert "Phase 8 guard 'test_guard' blocked ONBOARDING_APPROVE: unsafe change" in r.json()["error"]["message"]
    assert seen == {"action": "ONBOARDING_APPROVE", "object_type": "onboarding_session", "object_id": s["id"],
                    "actor": "anonymous"}
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "VALIDATED"  # nothing activated
    entry = last_audit(client, "ONBOARDING_APPROVE")
    assert entry["decision"] == "DENIED"
    assert entry["details"]["guards"] == [{"guard": GUARD, "verdict": "BLOCK", "reason": "unsafe change",
                                           "evidence": {"score": 0.1}}]


def test_elevated_review_requires_authenticated_soc_admin_with_note(client, users):
    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",),
                          lambda ctx: GuardDecision(elevated=True, reason="far from golden.", evidence={"similarity": 0.4}))
    s = validated_session(client, users["analyst"])
    assert approve(client, s).status_code == 403  # anonymous
    assert approve(client, s, users["eng1"], note="looks fine").status_code == 403  # not SOC_ADMIN
    r = approve(client, s, users["admin"])  # SOC_ADMIN without a note
    assert r.status_code == 403 and "written note" in r.json()["error"]["message"]
    r = approve(client, s, users["admin"], note="verified with the vendor change log")
    assert r.status_code == 200
    ok = last_audit(client, "ONBOARDING_APPROVE")
    assert ok["decision"] == "SUCCESS" and ok["actor"] == "admin"
    assert ok["details"]["guards"][0]["verdict"] == "ELEVATED"
    assert ok["details"]["request"]["note"] == "verified with the vendor change log"


def test_crashing_guard_fails_closed(client):
    def broken(ctx):
        raise RuntimeError("boom")

    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), broken)
    s = validated_session(client)
    r = approve(client, s)
    assert r.status_code == 409 and "failing closed" in r.json()["error"]["message"]
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "VALIDATED"


def test_allowing_guard_is_recorded_and_changes_nothing(client):
    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), lambda ctx: GuardDecision(reason="within limits"))
    s = validated_session(client)
    assert approve(client, s).status_code == 200
    assert last_audit(client, "ONBOARDING_APPROVE")["details"]["guards"][0]["verdict"] == "ALLOW"


def test_guards_run_after_rbac_and_maker_checker(client, users):
    calls = []
    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), lambda ctx: calls.append(1))
    s = validated_session(client, users["eng1"])
    assert approve(client, s, users["analyst"]).status_code == 403  # RBAC first
    assert approve(client, s, users["eng1"]).status_code == 403  # maker-checker first
    assert calls == []
    assert approve(client, s, users["eng2"]).status_code == 200 and calls == [1]


# --- H2: scheduler steps --------------------------------------------------------------------------------


def test_phase7_steps_run_first_and_unchanged(client):
    with TestSessionLocal() as db:
        baseline = scheduler.run_once(db)
    phase7 = ["raw_vault_backfill", "merkle_seal", "review_sla", "alerts"]
    assert [k for k in baseline if k not in ("started_at", "finished_at")][:4] == phase7
    scheduler.register_step("test_failing_step", lambda db: 1 / 0)
    scheduler.register_step("test_step", lambda db: {"ran": True})
    with TestSessionLocal() as db:
        result = scheduler.run_once(db)
    keys = [k for k in result if k not in ("started_at", "finished_at")]
    assert keys[:4] == phase7 and keys[-2:] == ["test_failing_step", "test_step"]
    assert result["test_failing_step"] == {"error": "ZeroDivisionError"} and result["test_step"] == {"ran": True}
    assert all("error" not in str(result[k]) for k in phase7)


# --- H3: persist hooks ----------------------------------------------------------------------------------


def test_persist_hook_runs_on_ingest_and_reprocess(client):
    calls = []
    evidence_service.register_persist_hook("test_hook", lambda db, e, reprocessed: calls.append((e.event_id, reprocessed)))
    e = ingest(client, '{"user":"a","action":"login"}')
    client.post(f"{API}/events/{e['event_id']}/reprocess")
    assert calls == [(e["event_id"], False), (e["event_id"], True)]


def test_failing_persist_hook_never_loses_evidence(client, monkeypatch):
    def failing(db, event, reprocessed):
        db.execute(text("INSERT INTO benchmark_runs (id) VALUES ('will-violate-not-null')"))  # real DB error

    evidence_service.register_persist_hook("test_failing_hook", failing)
    monkeypatch.setattr(get_settings(), "extension_inline_max_fields", 1)
    e = ingest(client, '{"user":"a","action":"login","x":1,"y":2,"z":3}')
    assert e["status"] in ("SUCCESS", "PARTIAL")
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM events WHERE event_id=:i"), {"i": e["event_id"]}).scalar() == 1
        assert db.execute(text("SELECT status FROM event_raw_storage WHERE event_id=:i"), {"i": e["event_id"]}).scalar() == "STORED"
        assert db.execute(text("SELECT count(*) FROM event_extension_overflow WHERE event_id=:i"), {"i": e["event_id"]}).scalar() == 1
        assert db.execute(text("SELECT count(*) FROM benchmark_runs")).scalar() == 0
    r = client.post(f"{API}/events/{e['event_id']}/reprocess")
    assert r.status_code == 200


# --- PHASE8_ENABLED switch --------------------------------------------------------------------------------


def test_disabled_phase8_registers_nothing(monkeypatch):
    marker = ("p8_marker", ("ONBOARDING_APPROVE",), lambda ctx: None)
    monkeypatch.setattr(p8, "GUARDS", [marker])
    monkeypatch.setattr(p8, "STEPS", [("p8_marker_step", lambda db: {})])
    monkeypatch.setattr(p8, "PERSIST_HOOKS", [("p8_marker_hook", lambda db, e, r: None)])
    assert p8.register_all() == {"guards": ["p8_marker"], "steps": ["p8_marker_step"], "persist_hooks": ["p8_marker_hook"]}
    assert any(n == "p8_marker" for n, _ in policy._GUARDS["ONBOARDING_APPROVE"])
    monkeypatch.setattr(get_settings(), "phase8_enabled", False)
    assert p8.register_all() == {"guards": [], "steps": [], "persist_hooks": []}
    assert not any(n == "p8_marker" for n, _ in policy._GUARDS.get("ONBOARDING_APPROVE", []))
    assert not any(n == "p8_marker_step" for n, _ in scheduler._EXTRA_STEPS)
    assert not any(n == "p8_marker_hook" for n, _ in evidence_service._PERSIST_HOOKS)
    p8.unregister_all()


# --- additional fail-closed coverage (Step 1 validation) ---------------------------------------------


def test_allowing_guard_lets_the_action_really_execute(client):
    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), lambda ctx: GuardDecision(reason="ok"))
    s = validated_session(client)
    assert approve(client, s).status_code == 200
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "APPROVED"


@pytest.mark.parametrize("note", ["", "   ", "\t\n"])
def test_elevated_review_rejects_empty_or_blank_note(client, users, note):
    policy.register_guard(GUARD, ("ONBOARDING_APPROVE",), lambda ctx: GuardDecision(elevated=True, reason="risky."))
    s = validated_session(client, users["analyst"])
    r = approve(client, s, users["admin"], note=note)
    assert r.status_code == 403 and "written note" in r.json()["error"]["message"]
    assert client.get(f"{API}/onboarding/sessions/{s['id']}").json()["status"] == "VALIDATED"


def test_hook_writes_are_rolled_back_when_the_hook_fails(client):
    def writes_then_fails(db, event, reprocessed):
        db.execute(text("INSERT INTO benchmark_runs (id, scenario, workload_size, variant, config, environment, "
                        "metrics, started_at, finished_at, tool_version) VALUES (:i, 'hook', 1, 'x', '{}', '{}', "
                        "'{}', now(), now(), 't')"), {"i": event.event_id})
        raise RuntimeError("after a successful write")

    evidence_service.register_persist_hook("test_failing_hook", writes_then_fails)
    e = ingest(client, '{"user":"a","action":"login"}')
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM benchmark_runs")).scalar() == 0  # hook work rolled back
        assert db.execute(text("SELECT count(*) FROM events WHERE event_id=:i"), {"i": e["event_id"]}).scalar() == 1
        assert db.execute(text("SELECT status FROM event_raw_storage WHERE event_id=:i"),
                          {"i": e["event_id"]}).scalar() == "STORED"


def _savepoint_origins(client, raw):
    """Ingest `raw` and return the app-level call sites of every SAVEPOINT issued."""
    import traceback

    from sqlalchemy import event as sa_event

    from tests.conftest import test_engine

    origins = []

    def capture(conn, cursor, statement, *args):
        if statement.upper().startswith("SAVEPOINT"):
            origins.append([f.name for f in traceback.extract_stack() if "/app/app/" in f.filename])

    sa_event.listen(test_engine, "before_cursor_execute", capture)
    try:
        e = ingest(client, raw)
    finally:
        sa_event.remove(test_engine, "before_cursor_execute", capture)
    return e, origins


def test_no_persist_hooks_means_no_hook_savepoint_and_identical_rows(client):
    # Phase 3 (runtime_registry) and Phase 5 (drift evaluate) already issue their own
    # savepoints during ingest; those are pre-existing and unchanged. The Phase 8
    # hook path must add none when nothing is registered, and exactly one per hook.
    assert evidence_service._PERSIST_HOOKS == []
    e, origins = _savepoint_origins(client, '{"user":"a","action":"login"}')
    assert not any("_run_persist_hooks" in o for o in origins)
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM event_raw_storage WHERE event_id=:i"), {"i": e["event_id"]}).scalar() == 1
        assert db.execute(text("SELECT count(*) FROM event_lineage_compact")).scalar() == 0  # nothing Phase 8 written
    # SQLAlchemy emits the SAVEPOINT lazily, i.e. once the hook touches the database.
    evidence_service.register_persist_hook("test_hook", lambda db, ev, reprocessed: db.execute(text("SELECT 1")))
    _, with_hook = _savepoint_origins(client, '{"user":"b","action":"login"}')
    assert sum("_run_persist_hooks" in o for o in with_hook) == 1
    assert len(with_hook) == len(origins) + 1
