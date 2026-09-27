"""Read-only operational intelligence layer (/api/v1/views): filters, cursor
pagination, summary, lineage (with SHA-256 integrity + field accounting),
sources and adapter-evolution timelines — all against the real API and
Postgres, and all strictly read-only."""
import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.routes import views as views_routes
from app.services import views_service
from tests.conftest import TestSessionLocal
from tests.integration.test_learning_flow import (  # noqa: F401 — fixtures/helpers reused
    ADAPTER, _settings, approve, base_log, drift_and_accept, propose, source, with_added,
)

API = "/api/v1"
V = f"{API}/views"
TABLES = ("events", "source_baselines", "source_baseline_history", "onboarding_sessions",
          "onboarded_adapters", "learning_sessions")
FORTI = ('<189>Jan 18 12:00:00 FGT100E FORTIGATE: date=2026-01-18 srcip=10.0.0.15 srcport=51422 '
         'dstip=8.8.8.8 dstport=53 proto=17 action="accept" level="notice" msg="dns"')
FORTI_DASH = FORTI.replace("dstport=53", "dstport=-")
CISCO = "<166>Jan 18 12:05:00 ciscoasa %ASA-6-302013: Built outbound TCP connection 1"


def ingest(client, raw):
    r = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert r.status_code == 201
    return r.json()


def get(client, path, **params):
    r = client.get(f"{V}{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def snapshot(db) -> dict:
    out = {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}
    out["events_updated"] = db.execute(text(
        "SELECT md5(string_agg(event_id || status || processing_metadata::text, ',' ORDER BY event_id)) FROM events")).scalar()
    return out


@pytest.fixture()
def loop(client, source):
    """Full adaptive loop on ACMEFW: v1 onboarded, drift accepted, v2 learned + active."""
    first, _ = drift_and_accept(client, [with_added(i) for i in range(6)])
    s = propose(client, first["event_id"])
    approve(client, s, activate=True)
    after = ingest(client, with_added(300))
    return {"history": source, "trigger": first, "session": s, "after": after}


# --- read-only guarantees ----------------------------------------------------------------------------


def test_every_views_endpoint_is_read_only(client, db_session, loop):
    ev = loop["after"]["event_id"]
    before = snapshot(db_session)
    for path, params in (("/events", {}), ("/events", {"include_total": "true"}), ("/summary", {}), ("/filters", {}),
                         (f"/events/{ev}/lineage", {}), ("/sources", {}), (f"/sources/{ADAPTER}", {}),
                         (f"/sources/{ADAPTER}/timeline", {})):
        get(client, path, **params)
    db_session.expire_all()
    assert snapshot(db_session) == before


def test_readonly_transaction_rejects_writes():
    db = TestSessionLocal()
    try:
        gen = views_routes.get_readonly_db(db)
        session = next(gen)
        with pytest.raises(DBAPIError) as err:
            session.execute(text("DELETE FROM events"))
        assert "read-only" in str(err.value).lower()
        gen.close()
    finally:
        db.close()


def test_views_layer_never_invokes_pipeline_or_workflows():
    from app.db.repository import views_repo

    source_code = inspect.getsource(views_service) + inspect.getsource(views_routes) + inspect.getsource(views_repo)
    # No pipeline, no workflow services (onboarding/drift/learning/ingestion), no writes.
    for forbidden in ("orchestrator", "normalizer", "run_pipeline", "ingestion_service", "onboarding_service",
                      "drift_service", "learning_service", "event_repo", ".commit(", "db.add(", ".delete(",
                      "insert(", "update("):
        assert forbidden not in source_code, forbidden
    # The only transaction control is ending the READ ONLY transaction.
    assert source_code.count("rollback(") == 1 and "SET TRANSACTION READ ONLY" in source_code


# --- event query --------------------------------------------------------------------------------------


def test_event_filters(client, loop):
    forti = ingest(client, FORTI)
    dash = ingest(client, FORTI_DASH)
    ingest(client, "not a log at all")

    by = lambda **p: [e["event_id"] for e in get(client, "/events", limit=200, **p)["items"]]  # noqa: E731
    assert set(by(status="PARTIAL")) == {dash["event_id"]}
    assert all(e["status"] == "FAILED" for e in get(client, "/events", status="FAILED")["items"])
    assert set(by(vendor="Fortinet")) == {forti["event_id"], dash["event_id"]}
    assert set(by(format="unknown")) and all(e["format_detected"] == "unknown" for e in get(client, "/events", format="unknown")["items"])
    assert loop["after"]["event_id"] in by(source=ADAPTER, adapter_version="2")
    assert loop["history"][0]["event_id"] in by(adapter_version="1", adapter_id=ADAPTER)
    assert by(drift_status="DRIFT") == [loop["trigger"]["event_id"]]
    assert forti["event_id"] in by(drift_status="BASELINE_CREATED")  # first Fortinet event bootstraps its baseline
    assert loop["trigger"]["event_id"] in by(drift_severity=loop["trigger"]["processing_metadata"]["drift"]["severity"])
    assert set(by(category="Network Activity")) >= {forti["event_id"]}
    assert by(search=dash["raw_hash"]) == [dash["event_id"]]
    assert by(search=dash["event_id"]) == [dash["event_id"]]
    assert set(by(search="dstport=-")) == {dash["event_id"]}
    assert set(by(search="100%_")) == set()  # LIKE wildcards are escaped


def test_event_row_shape(client):
    dash = ingest(client, FORTI_DASH)
    row = get(client, "/events", search=dash["event_id"])["items"][0]
    assert row["source_key"] == "fortinet" and row["adapter_source"] == "manual"
    assert row["warning_count"] == 1 and row["preserved_field_count"] >= 1
    assert "raw_event" not in row and row["raw_hash"] == dash["raw_hash"]


def test_time_range_filter(client, db_session):
    events = [ingest(client, CISCO + f" #{i}") for i in range(3)]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, e in enumerate(events):
        db_session.execute(text("UPDATE events SET received_at = :t WHERE event_id = :i"),
                           {"t": base + timedelta(days=i), "i": e["event_id"]})
    db_session.commit()
    items = get(client, "/events", start=(base + timedelta(days=1)).isoformat(), end=(base + timedelta(days=2)).isoformat())["items"]
    assert [e["event_id"] for e in items] == [events[1]["event_id"]]
    bad = client.get(f"{V}/events", params={"start": base.isoformat(), "end": base.isoformat()})
    assert bad.status_code == 422


def test_cursor_pagination_is_complete_and_stable(client):
    ids = {ingest(client, CISCO + f" page {i}")["event_id"] for i in range(25)}
    seen: list[str] = []
    page = get(client, "/events", limit=10, include_total="true")
    assert page["total"] == 25
    while True:
        seen += [e["event_id"] for e in page["items"]]
        ingest(client, CISCO + " inserted during paging")  # newer events must not shift older pages
        if not page["has_more"]:
            break
        page = get(client, "/events", limit=10, cursor=page["next_cursor"])
    assert len(seen) == len(set(seen)) == 25 and set(seen) == ids
    times = [e["received_at"] for e in get(client, "/events", limit=200)["items"]]
    assert times == sorted(times, reverse=True)


def test_invalid_cursor_and_params(client):
    assert client.get(f"{V}/events", params={"cursor": "not-a-cursor"}).status_code == 422
    assert client.get(f"{V}/events", params={"limit": 0}).status_code == 422
    assert client.get(f"{V}/events", params={"search": "ab"}).status_code == 422


# --- summary / filters ------------------------------------------------------------------------------


def test_summary_counts_match_the_database(client, db_session, loop):
    ingest(client, FORTI_DASH)
    ingest(client, "garbage")
    s = get(client, "/summary", bucket="hour")
    db = lambda q: db_session.execute(text(q)).scalar_one()  # noqa: E731
    assert s["totals"]["events"] == db("SELECT count(*) FROM events")
    assert s["by_status"]["PARTIAL"] == db("SELECT count(*) FROM events WHERE status='PARTIAL'")
    assert s["totals"]["unique_sources"] == db("SELECT count(DISTINCT adapter_id) FROM events")
    assert s["totals"]["pending_reviews"] == db("SELECT count(*) FROM events WHERE status='UNDER_REVIEW'")
    assert s["totals"]["drift_events"] == db("SELECT count(*) FROM events WHERE processing_metadata->'drift'->>'status' IN ('DRIFT','POSSIBLE_FORMAT_DRIFT')")
    assert s["adapters"] == {"shipped_vendor": 3, "shipped_generic": 5, "onboarded_active": 1}
    assert s["learning_sessions"] == {"ACTIVE": 1} and s["onboarding_sessions"] == {"APPROVED": 1}
    assert sum(b["total"] for b in s["trend"]) == s["totals"]["events"]
    assert get(client, "/summary", status="FAILED")["totals"]["events"] == s["by_status"]["FAILED"]


def test_filter_values(client, db_session, loop):
    ingest(client, FORTI)
    f = get(client, "/filters")
    assert f["statuses"] == sorted(db_session.execute(text("SELECT DISTINCT status FROM events")).scalars().all())
    assert "Fortinet" in f["vendors"] and ADAPTER in f["sources"] and "kv" in f["formats"]
    assert {"adapter_id": ADAPTER, "adapter_version": "2", "events": 1} in f["adapters"]
    assert "DRIFT" in f["drift_statuses"]


# --- lineage -----------------------------------------------------------------------------------------

STAGES = ["RAW", "FORMAT_DETECTION", "PARSER", "ADAPTER", "NORMALIZATION", "FIELD_ACCOUNTING", "WARNINGS",
          "DRIFT_DECISION", "BASELINE", "LEARNING_HISTORY"]


def test_lineage_of_a_normal_event(client):
    e = ingest(client, FORTI)
    L = get(client, f"/events/{e['event_id']}/lineage")
    assert [s["stage"] for s in L["chain"]] == STAGES
    assert L["integrity"] == {"algorithm": "SHA-256", "stored": e["raw_hash"], "recomputed": e["raw_hash"],
                              "verified": True, "raw_bytes": len(FORTI.encode())}
    fa = L["field_accounting"]
    assert fa["parsed_count"] == fa["mapped_count"] + fa["preserved_count"] and fa["unaccounted"] == []
    by_field = {f["field"]: f for f in fa["fields"]}
    assert by_field["srcip"] == {"field": "srcip", "outcome": "MAPPED", "target": "network.src_ip", "normalized_value_present": True}
    assert L["nothing_silently_discarded"] is True
    adapter = next(s for s in L["chain"] if s["stage"] == "ADAPTER")
    assert adapter["details"]["origin"] == "shipped_yaml"


def test_lineage_proves_nothing_was_discarded_for_a_bad_value(client):
    e = ingest(client, FORTI_DASH)
    L = get(client, f"/events/{e['event_id']}/lineage")
    dst = next(f for f in L["field_accounting"]["fields"] if f["field"] == "dstport")
    assert dst == {"field": "dstport", "outcome": "PRESERVED", "location": "extensions.dstport", "value": "-"}
    warn = next(s for s in L["chain"] if s["stage"] == "WARNINGS")
    assert warn["outcome"] == "WARN" and warn["details"]["warnings"][0]["kind"] == "TYPE_MISMATCH_PRESERVED"
    assert next(s for s in L["chain"] if s["stage"] == "NORMALIZATION")["outcome"] == "WARN"
    assert L["nothing_silently_discarded"] is True


def test_lineage_of_a_failed_event(client):
    raw = "totally unknown text"
    e = ingest(client, raw)
    L = get(client, f"/events/{e['event_id']}/lineage")
    outcomes = {s["stage"]: s["outcome"] for s in L["chain"]}
    assert outcomes["RAW"] == "OK" and outcomes["FORMAT_DETECTION"] == "FAIL" and outcomes["PARSER"] == "FAIL"
    assert outcomes["FIELD_ACCOUNTING"] == "SKIPPED" and L["nothing_silently_discarded"] is True
    assert L["integrity"]["recomputed"] == hashlib.sha256(raw.encode()).hexdigest()


def test_lineage_detects_tampered_raw_event(client, db_session):
    e = ingest(client, FORTI)
    db_session.execute(text("UPDATE events SET raw_event = raw_event || ' tampered' WHERE event_id = :i"), {"i": e["event_id"]})
    db_session.commit()
    L = get(client, f"/events/{e['event_id']}/lineage")
    assert L["integrity"]["verified"] is False and L["chain"][0]["outcome"] == "FAIL"
    assert L["nothing_silently_discarded"] is False


def test_lineage_through_the_adaptive_loop(client, loop):
    old = get(client, f"/events/{loop['history'][0]['event_id']}/lineage")
    adapter = next(s for s in old["chain"] if s["stage"] == "ADAPTER")
    assert adapter["outcome"] == "WARN" and adapter["details"]["version"] == 1
    assert adapter["details"]["version_status_now"] == "SUPERSEDED" and adapter["details"]["active_version_now"] == 2

    new = get(client, f"/events/{loop['after']['event_id']}/lineage")
    adapter = next(s for s in new["chain"] if s["stage"] == "ADAPTER")
    assert adapter["details"]["origin"] == "phase6_learning" and adapter["details"]["session_id"] == loop["session"]["id"]
    mapped = {f["field"]: f.get("target") for f in new["field_accounting"]["fields"] if f["outcome"] == "MAPPED"}
    assert mapped["username"] == "user.name"
    assert next(s for s in new["chain"] if s["stage"] == "BASELINE")["details"]["relation"] == "the reference structure"

    trig = get(client, f"/events/{loop['trigger']['event_id']}/lineage")
    hist = next(s for s in trig["chain"] if s["stage"] == "LEARNING_HISTORY")["details"]["learning_sessions"]
    assert hist[0]["role"] == "trigger" and hist[0]["learning_session_id"] == loop["session"]["id"]
    drift = next(s for s in trig["chain"] if s["stage"] == "DRIFT_DECISION")
    assert drift["details"]["status"] == "DRIFT" and drift["details"]["review"]["resolution"] == "accepted_variant"


def test_views_read_legacy_unreadable_rows(client, db_session):
    """Rows stored before the typed-field fix (invalid typed values) break
    GET /events; the views layer reads columns directly and stays available."""
    e = ingest(client, FORTI)
    db_session.execute(text("UPDATE events SET network = CAST(:n AS jsonb) WHERE event_id = :i"),
                       {"n": json.dumps({"dst_port": "-"}), "i": e["event_id"]})
    db_session.commit()
    assert e["event_id"] in [x["event_id"] for x in get(client, "/events")["items"]]
    assert get(client, f"/events/{e['event_id']}/lineage")["integrity"]["verified"] is True


def test_lineage_404(client):
    assert client.get(f"{V}/events/01ARZ3NDEKTSV4RRFFQ69G5FAV/lineage").status_code == 404


# --- sources + timeline -------------------------------------------------------------------------------


def test_sources(client, loop):
    ingest(client, FORTI)
    ingest(client, FORTI_DASH)
    items = {s["source_key"]: s for s in get(client, "/sources")["items"]}
    acme, forti = items[ADAPTER], items["fortinet"]
    assert acme["kind"] == "onboarded" and acme["active_version"] == "2"
    assert acme["adapter_versions_seen"] == {"1": 16, "2": 1}  # 10 history + 6 drift-phase on v1; 1 after learning
    assert acme["baseline"]["accepted_variants"] >= 1 and acme["learning_sessions"] == {"ACTIVE": 1}
    assert forti["kind"] == "shipped_vendor" and forti["events"]["total"] == 2 and forti["partial_rate"] == 0.5
    d = get(client, f"/sources/{ADAPTER}")
    assert [(v["version"], v["status"], v["origin"]) for v in d["versions"]] == [
        (1, "SUPERSEDED", "phase3_onboarding"), (2, "ACTIVE", "phase6_learning")]
    assert [h["action"] for h in d["baseline_history"]][-1] == "BASELINE_LEARNED"
    assert d["recent_drift"][0]["drift_status"] == "DRIFT"
    assert client.get(f"{V}/sources/nope").status_code == 404


def test_adapter_evolution_timeline(client, loop):
    t = get(client, f"/sources/{ADAPTER}/timeline")
    kinds = [e["kind"] for e in t["entries"]]
    for expected in ("ONBOARDING_STARTED", "ONBOARDING_APPROVED", "VERSION_ACTIVATED", "BASELINE_CREATED", "DRIFT",
                     "DRIFT_REVIEWED", "LEARNING_PROPOSED", "LEARNING_APPROVED", "LEARNING_ACTIVATED",
                     "BASELINE_LEARNED", "VERSION_SUPERSEDED"):
        assert expected in kinds, expected
    times = [e["at"] for e in t["entries"]]
    assert times == sorted(times)
    assert kinds.index("ONBOARDING_APPROVED") < kinds.index("DRIFT") < kinds.index("LEARNING_ACTIVATED")
    # the learned version becomes active at activation, after approval
    v2_at = [e["at"] for e in t["entries"] if e["kind"] == "VERSION_ACTIVATED" and e["refs"]["version"] == 2][0]
    approved_at = [e["at"] for e in t["entries"] if e["kind"] == "LEARNING_APPROVED"][0]
    assert v2_at >= approved_at
    v2 = [e for e in t["entries"] if e["kind"] == "VERSION_ACTIVATED" and e["refs"]["version"] == 2][0]
    assert "phase6 learning" in v2["title"] and v2["refs"]["session_id"] == loop["session"]["id"]
    assert client.get(f"{V}/sources/nope/timeline").status_code == 404


def test_shipped_source_timeline_has_baseline_only(client):
    ingest(client, FORTI)
    t = get(client, "/sources/fortinet/timeline")
    assert [e["kind"] for e in t["entries"]] == ["BASELINE_CREATED"]
