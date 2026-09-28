"""Phase 8 Step 4: statistical drift -> semantic advisories on stored events.

Events are inserted directly with controlled received_at so the baseline
(168 h) and current (1 h) windows are exact. Phase 5 structural drift, its
API and its state are never touched."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.pipeline.hashing import sha256_hex
from app.services.phase8 import advanced_drift_service as svc
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, _phase7_defaults, isolated_stores, users  # noqa: F401

END = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
SOURCE = "synthetic_fw"
REQUIRED = {"id", "layer", "source", "field", "metric", "baseline_value", "current_value", "threshold", "severity",
            "explanation", "evidence_counts", "analysis_window", "deterministic_reason", "deviation"}


def make(action="allow", severity="low", proto="tcp", port=443, direction="inbound", domain="corp", *,
         hours_before_end: float, source=SOURCE, signature="sigA", drift_status=None, extensions=None, i=0):
    raw = f"{source} {action} {severity} {proto} {port} {direction} {domain} {hours_before_end} {i}"
    pm = {"drift": {"status": drift_status, "source_key": source}} if drift_status else {}
    return Event(event_id=generate_event_id(), raw_event=raw, raw_hash=sha256_hex(raw),
                 received_at=END - timedelta(hours=hours_before_end), format_detected="kv", status="SUCCESS",
                 adapter_id=source, event_action=action, severity=severity,
                 network={"protocol": proto, "dst_port": port, "direction": direction}, user={"domain": domain},
                 extensions=extensions or {}, processing_metadata=pm, warnings=[],
                 structural_fingerprint={"signature": signature})


def insert(events):
    with TestSessionLocal() as db:
        db.add_all(events)
        db.commit()


def baseline_events(n=400, **kw):
    return [make(action="deny" if i % 2 else "allow", severity="high" if i % 2 else "low",
                 port=443 if i % 2 else 80, direction="inbound" if i % 2 else "outbound",
                 hours_before_end=2 + (i % 160), i=i, **kw) for i in range(n)]


def current_events(n=300, relabel=None, **kw):
    out = []
    for i in range(n):
        action = "deny" if i % 2 else "allow"
        if relabel and action in relabel:
            action = relabel[action]
        out.append(make(action=action, severity="high" if i % 2 else "low", port=443 if i % 2 else 80,
                        direction="inbound" if i % 2 else "outbound", hours_before_end=0.5 * (i + 1) / n, i=i, **kw))
    return out


def analyze(client, headers=None, **body):
    r = client.post(f"{API}/drift/statistical/analyze",
                    json={"source_key": SOURCE, "window_end": END.isoformat(), **body}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_insufficient_samples_are_skipped_not_guessed(client):
    insert(baseline_events(400) + current_events(199))
    report = analyze(client)
    src = report["sources"][0]
    assert src["status"] == "SKIPPED_INSUFFICIENT_SAMPLES" and src["current_n"] == 199
    assert report["findings"] == 0 and client.get(f"{API}/drift/findings").json()["items"] == []


def test_stable_source_produces_no_findings(client):
    insert(baseline_events() + current_events())
    report = analyze(client)
    src = report["sources"][0]
    assert src["status"] == "ANALYZED" and src["findings"] == [] and src["advisories"] == []
    assert len(src["evaluated"]) >= 6 * 3  # every monitored field was evaluated and reported


def test_relabel_produces_statistical_findings_then_a_semantic_advisory(client):
    insert(baseline_events() + current_events(relabel={"deny": "blocked"}))
    report = analyze(client)
    src = report["sources"][0]
    metrics = {(f["field"], f["metric"]) for f in src["findings"]}
    assert ("event_action", "PSI") in metrics and ("event_action", "NEW_CATEGORY_SHARE") in metrics
    assert not any(f["field"] != "event_action" for f in src["findings"])  # related fields stable
    for f in src["findings"]:
        assert REQUIRED <= set(f) and f["layer"] == "STATISTICAL" and f["advisory"] is False
        assert f["evidence_counts"] == {"baseline_events": 400, "current_events": 300}
        assert f["analysis_window"]["current_end"] == END.isoformat()
        assert f["severity"] in ("MEDIUM", "HIGH") and f["deviation"] >= f["threshold"]
        assert f["deterministic_reason"] == f"{f['metric']}_AT_OR_ABOVE_THRESHOLD"
    [adv] = src["advisories"]
    assert adv["layer"] == "SEMANTIC" and adv["advisory"] is True and adv["severity"] == "ADVISORY"
    assert adv["metric"] == "ACTION_RELABEL_ADVISORY"
    assert adv["evidence"]["pairs"][0]["baseline_value"] == "deny" and adv["evidence"]["pairs"][0]["current_value"] == "blocked"
    parent = client.get(f"{API}/drift/findings/{adv['parent_finding_id']}").json()
    assert parent["layer"] == "STATISTICAL" and parent["field"] == "event_action"
    assert [c["id"] for c in parent["children"]] == [adv["id"]]


def test_semantic_needs_stable_structure(client):
    cur = current_events(relabel={"deny": "blocked"}, signature="sigB")  # structure changed too
    insert(baseline_events() + cur)
    src = analyze(client)["sources"][0]
    assert src["findings"] and src["advisories"] == []


def test_semantic_never_runs_without_statistical_evidence(client, monkeypatch):
    from app.phase8 import drift_semantic

    calls = []
    monkeypatch.setattr(drift_semantic, "action_relabel", lambda *a, **k: calls.append(1))
    insert(baseline_events() + current_events())
    analyze(client)
    assert calls == []


def test_rerun_is_idempotent_and_deterministic(client):
    insert(baseline_events() + current_events(relabel={"deny": "blocked"}))
    first = analyze(client)["sources"][0]
    second = analyze(client)["sources"][0]
    strip = lambda fs: [{k: v for k, v in f.items() if k != "created_at"} for f in fs]  # noqa: E731
    assert strip(first["findings"]) == strip(second["findings"]) and first["advisories"] == second["advisories"]
    stored = client.get(f"{API}/drift/findings").json()
    assert len(stored["items"]) == len(first["findings"]) + len(first["advisories"])


def test_numeric_null_and_cardinality_signals(client):
    base = baseline_events()
    cur = [make(port=8443, direction=None, domain=f"d{i}", hours_before_end=0.5 * (i + 1) / 300, i=i) for i in range(300)]
    insert(base + cur)
    metrics = {(f["field"], f["metric"]) for f in analyze(client)["sources"][0]["findings"]}
    assert ("network.dst_port", "MEDIAN_SHIFT_IQR") in metrics
    assert ("network.direction", "NULL_RATE_CHANGE") in metrics
    assert ("user.domain", "CARDINALITY_RATIO") in metrics


def test_opt_in_extension_fields(client):
    base = [make(hours_before_end=2 + i % 100, extensions={"rule": "r1"}, i=i) for i in range(250)]
    cur = [make(hours_before_end=0.5 * (i + 1) / 250, extensions={"rule": "r9"}, i=i) for i in range(250)]
    insert(base + cur)
    assert analyze(client)["sources"][0]["findings"] == []  # extensions are opt-in
    findings = analyze(client, extension_fields=["rule"])["sources"][0]["findings"]
    assert ("extensions.rule", "PSI") in {(f["field"], f["metric"]) for f in findings}
    r = client.post(f"{API}/drift/statistical/analyze", json={"extension_fields": [f"f{i}" for i in range(11)]})
    assert r.status_code == 422


def test_work_is_bounded(client, monkeypatch):
    monkeypatch.setattr(svc, "MAX_EVENTS_PER_WINDOW", 250)
    insert(baseline_events() + current_events())
    src = analyze(client)["sources"][0]
    assert src["baseline_n"] == 250 and src["baseline_truncated"] is True and src["current_truncated"] is True


def test_all_sources_mode_and_default_windows(client):
    insert(baseline_events() + current_events() + [make(hours_before_end=0.2, source="other_src")])
    r = client.post(f"{API}/drift/statistical/analyze", json={"window_end": END.isoformat()}).json()
    assert [s["source_key"] for s in r["sources"]] == ["other_src", SOURCE]
    assert r["config"]["baseline_hours"] == 168 and r["config"]["current_hours"] == 1
    assert r["config"]["min_sample"] == 200 and r["config"]["thresholds"]["PSI"]["threshold"] == 0.25


def test_phase5_drift_state_and_api_untouched(client):
    insert(baseline_events() + current_events(relabel={"deny": "blocked"}))
    with TestSessionLocal() as db:
        before = db.execute(text("SELECT event_id, status, processing_metadata FROM events ORDER BY event_id")).all()
    baselines_before = client.get(f"{API}/drift/baselines").json()
    analyze(client)
    with TestSessionLocal() as db:
        assert db.execute(text("SELECT event_id, status, processing_metadata FROM events ORDER BY event_id")).all() == before
        assert db.execute(text("SELECT count(*) FROM source_baselines")).scalar() == 0
    assert client.get(f"{API}/drift/baselines").json() == baselines_before


def test_acknowledge_is_audited_and_rbac_enforced(client, users, monkeypatch):
    insert(baseline_events() + current_events(relabel={"deny": "blocked"}))
    finding = analyze(client, users["analyst"])["sources"][0]["findings"][0]
    r = client.post(f"{API}/drift/findings/{finding['id']}/acknowledge", json={"note": "vendor renamed deny"},
                    headers=users["analyst"])
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED" and r.json()["acknowledged_by"] == "analyst"
    audit = client.get(f"{API}/governance/audit", params={"action": "DRIFT_FINDING_ACKNOWLEDGE"},
                       headers=users["admin"]).json()["items"][0]
    assert audit["decision"] == "SUCCESS" and audit["actor"] == "analyst"
    assert analyze(client)["sources"][0]["findings"][0]["status"] == "ACKNOWLEDGED"  # rerun keeps the review
    monkeypatch.setattr(get_settings(), "rbac_mode", "enforce")
    r = client.post(f"{API}/drift/statistical/analyze", json={"source_key": SOURCE})
    assert r.status_code == 401
    denied = client.get(f"{API}/governance/audit", params={"action": "DRIFT_STATISTICAL_ANALYZE"},
                        headers=users["admin"]).json()["items"][0]
    assert denied["decision"] == "DENIED"


def test_unknown_finding_is_404(client):
    assert client.get(f"{API}/drift/findings/nope").status_code == 404
    assert client.post(f"{API}/drift/findings/nope/acknowledge", json={}).status_code == 404


@pytest.mark.parametrize("hours", [0, 24 * 7 + 1])
def test_window_bounds_validated(client, hours):
    r = client.post(f"{API}/drift/statistical/analyze", json={"current_hours": hours})
    assert r.status_code == 422
