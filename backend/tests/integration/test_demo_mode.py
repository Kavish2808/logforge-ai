"""Demo Mode end-to-end: the real runner, through the public API only,
against the test database — including pre-existing non-demo data that must
survive every run and every reset untouched."""
import pytest
from sqlalchemy import text

from app.demo import fixtures as fx
from app.demo.runner import DemoRunner

API = "/api/v1"
TABLES = {
    "events": "event_id", "onboarding_sessions": "id", "onboarded_adapters": "id",
    "learning_sessions": "id", "source_baselines": "source_key", "source_baseline_history": "id",
}


def _acme(raw: str) -> str:
    return raw.replace("LogForgeDemo", "AcmeCorp").replace(fx.DEMO_MARKER, "acme-fw-01")


def _snapshot(db, exclude_demo: bool = True) -> dict[str, list[str]]:
    """Every non-demo row of every table the demo can touch, as text."""
    db.expire_all()
    out = {}
    for table, key in TABLES.items():
        rows = db.execute(text(f"SELECT t::text, t.* FROM {table} t ORDER BY t.{key}")).mappings().all()
        keep = []
        for r in rows:
            if exclude_demo and (
                (table == "events" and r["raw_hash"] in fx.raw_hashes())
                or (table == "onboarding_sessions" and r["name"] == fx.DEMO_ID)
                or (table == "onboarded_adapters" and r["adapter_id"] == fx.DEMO_SOURCE_KEY)
                or (table in ("learning_sessions", "source_baselines", "source_baseline_history") and r["source_key"] == fx.DEMO_SOURCE_KEY)
            ):
                continue
            keep.append(r["t"])
        out[table] = keep
    db.rollback()
    return out


@pytest.fixture()
def existing_user_data(client):
    """Non-demo state in every table: a shipped-vendor event + baseline, an onboarded
    adapter with its session, a drift and an accepted learning session."""
    ev = client.post(f"{API}/ingest", json={"raw_log": '<189>Jan 18 12:00:00 FGT100E FORTIGATE: date=2026-01-18 time=12:00:00 devname="FGT100E" srcip=10.0.0.15 srcport=5 dstip=8.8.8.8 dstport=53 proto=17 action="accept"'})
    assert ev.status_code == 201
    samples = [_acme(fx.v1_log(i)) for i in range(12)]
    s = client.post(f"{API}/onboarding/sessions", json={"name": "acme-onboarding", "samples": samples}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    assert client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"], "adapter_id": "acme_fw"}).status_code == 200
    for i in range(8):
        client.post(f"{API}/ingest", json={"raw_log": _acme(fx.v1_log(700 + i))})
    drift = client.post(f"{API}/ingest", json={"raw_log": _acme(fx.v2_log(800))}).json()
    assert drift["status"] == "UNDER_REVIEW"
    client.post(f"{API}/events/{drift['event_id']}/drift/accept", json={"mode": "add_variant"})
    for i in range(3):
        client.post(f"{API}/ingest", json={"raw_log": _acme(fx.v2_log(801 + i))})
    L = client.post(f"{API}/events/{drift['event_id']}/learning/propose", json={"assistant": "offline"})
    assert L.status_code == 201
    return {"session_id": s["id"], "learning_id": L.json()["id"]}


def _status(client):
    return client.get(f"{API}/demo/status").json()


def test_full_demo_passes_and_leaves_existing_data_untouched(client, db_session, existing_user_data):
    before = _snapshot(db_session)
    assert all(before.values()), "fixture must create non-demo rows in every table"
    lines = []
    assert DemoRunner(client, out=lines.append).run(), "\n".join(lines)
    assert sum(line.startswith("[PASS]") for line in lines) == 23
    assert _snapshot(db_session) == before

    st = _status(client)
    assert st["complete"] and st["conflict"] is None
    assert [s["state"] for s in st["steps"]] == ["done"] * 15
    assert [(v["version"], v["status"]) for v in st["adapter_versions"]] == [(1, "ACTIVE"), (2, "ROLLED_BACK")]
    assert st["learning_session"]["status"] == "ROLLED_BACK"
    assert st["events"]["unknown_probe"]["status"] == "FAILED"
    assert st["events"]["malformed"]["status"] == "FAILED"
    assert st["events"]["check_v1"]["adapter_version"] == "2"  # old structure under v2
    assert st["events"]["post_rollback_v1"]["adapter_version"] == "1"


def test_every_human_decision_is_a_real_boundary(client):
    """At each decide() call nothing has been approved yet, and the status
    machine reports that exact step as a HUMAN decision."""
    seen = []

    def decide(prompt):
        st = _status(client)
        seen.append((st["next"]["kind"], st["next"]["step"], st["next"]["action"]))
        adapter = client.get(f"{API}/onboarding/adapters/{fx.DEMO_SOURCE_KEY}")
        if st["next"]["step"] == "approve_v1":
            assert adapter.status_code == 404  # nothing is active before the human approves
            assert st["onboarding_session"]["status"] == "VALIDATED"
        if st["next"]["step"] == "drift_accepted":
            assert st["events"]["drift_trigger"]["status"] == "UNDER_REVIEW"
        if st["next"]["step"] == "approve_v2":
            assert [v["version"] for v in adapter.json()["versions"]] == [1]
        if st["next"]["step"] == "adapter_v2_active":
            assert [(v["version"], v["status"]) for v in adapter.json()["versions"]] == [(1, "ACTIVE")]  # v2 exists only once activated
            assert st["learning_session"]["status"] == "APPROVED"
        if st["next"]["step"] == "rollback_verified":
            assert st["learning_session"]["status"] == "ACTIVE"

    assert DemoRunner(client, decide=decide, out=lambda _: None).run()
    assert seen == [
        ("human", "approve_v1", "approve_onboarding"),
        ("human", "drift_accepted", "accept_drift"),
        ("human", "approve_v2", "approve_learning"),
        ("human", "adapter_v2_active", "activate_learning"),
        ("human", "rollback_verified", "rollback_learning"),
    ]


def test_status_machine_starts_with_the_automatic_probe(client):
    st = _status(client)
    assert not st["exists"] and not st["complete"]
    assert st["next"] == {"step": "unknown_source", "kind": "pending", "action": "ingest_probe"}
    assert [s["state"] for s in st["steps"]][:2] == ["pending", "waiting"]


def test_repeated_runs_are_idempotent(client, db_session, existing_user_data):
    before = _snapshot(db_session)
    runner_out = []
    assert DemoRunner(client, out=runner_out.append).run()
    first = _status(client)
    assert DemoRunner(client, out=runner_out.append).run()
    second = _status(client)
    counts = db_session.execute(text(
        "SELECT (SELECT count(*) FROM onboarding_sessions WHERE name = :n), "
        "(SELECT count(*) FROM onboarded_adapters WHERE adapter_id = :k), "
        "(SELECT count(*) FROM learning_sessions WHERE source_key = :k), "
        "(SELECT count(*) FROM events WHERE raw_hash = ANY(:h))"),
        {"n": fx.DEMO_ID, "k": fx.DEMO_SOURCE_KEY, "h": list(fx.raw_hashes())}).one()
    db_session.rollback()
    assert tuple(counts) == (1, 2, 1, 20)  # exactly one run's worth of demo state
    assert [s["state"] for s in first["steps"]] == [s["state"] for s in second["steps"]] == ["done"] * 15
    assert _snapshot(db_session) == before


def test_reset_removes_only_demo_state_and_is_idempotent(client, db_session, existing_user_data):
    before = _snapshot(db_session)
    assert DemoRunner(client, out=lambda _: None).run()
    r1 = client.post(f"{API}/demo/reset").json()
    assert r1["deleted"] == {"learning_sessions": 1, "events": 20, "adapter_versions": 2, "onboarding_sessions": 1,
                             "baselines": 1, "baseline_history": 3}
    assert r1["non_demo_rows"]["unchanged"] and r1["non_demo_rows"]["before"] == r1["non_demo_rows"]["after"]
    r2 = client.post(f"{API}/demo/reset").json()
    assert set(r2["deleted"].values()) == {0}
    assert _snapshot(db_session) == before
    assert _snapshot(db_session, exclude_demo=False) == before  # no demo rows remain at all
    assert not _status(client)["exists"]
    # the user's adapter and learning session still work through the API
    assert client.get(f"{API}/onboarding/adapters/acme_fw").status_code == 200
    assert client.get(f"{API}/learning/sessions/{existing_user_data['learning_id']}").status_code == 200


def test_reset_refuses_when_the_demo_source_key_is_not_demo_owned(client, db_session):
    samples = [_acme(fx.v1_log(i)) for i in range(12)]
    s = client.post(f"{API}/onboarding/sessions", json={"name": "not-the-demo", "samples": samples}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    ok = client.post(f"{API}/onboarding/sessions/{s['id']}/approve",
                     json={"proposal_version": s["proposal_version"], "adapter_id": fx.DEMO_SOURCE_KEY})
    assert ok.status_code == 200
    before = _snapshot(db_session, exclude_demo=False)
    r = client.post(f"{API}/demo/reset")
    assert r.status_code == 409
    assert "not created by the demo" in r.json()["error"]["message"]
    assert _status(client)["conflict"]
    lines = []
    assert not DemoRunner(client, out=lines.append).run()
    assert lines[1].startswith("[FAIL]") and "HTTP 409" in lines[1]
    assert _snapshot(db_session, exclude_demo=False) == before


def test_demo_session_name_alone_does_not_grant_ownership(client, db_session):
    """A user session that happens to use the demo name, with non-demo samples, is never deleted."""
    samples = [_acme(fx.v1_log(i)) for i in range(12)]
    assert client.post(f"{API}/onboarding/sessions", json={"name": fx.DEMO_ID, "samples": samples}).status_code == 201
    before = _snapshot(db_session, exclude_demo=False)
    assert client.post(f"{API}/demo/reset").json()["deleted"]["onboarding_sessions"] == 0
    assert _snapshot(db_session, exclude_demo=False) == before


def test_fixtures_endpoint_serves_the_exact_fixtures(client):
    body = client.get(f"{API}/demo/fixtures").json()
    assert body["namespace"]["source_key"] == fx.DEMO_SOURCE_KEY
    assert [(f["key"], f["raw"], f["sha256"]) for f in body["fixtures"]] == [(f.key, f.raw, f.sha256) for f in fx.FIXTURES]
