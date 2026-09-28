"""Phase 8 Step 8: cross-vendor drift correlation — deterministic,
decomposable, investigation-only."""
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.phase8 import DriftFinding
from app.pipeline.hashing import sha256_hex
from app.services.phase8 import correlation_service as svc
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, _phase7_defaults, isolated_stores, users  # noqa: F401

END = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
UNTOUCHED = ("events", "source_baselines", "source_baseline_history", "onboarded_adapters", "learning_sessions",
             "golden_baselines", "drift_findings", "event_revisions", "replay_jobs")


def drift_event(source, vendor, *, minutes_before_end=10, added=(), removed=(), change_types=("FIELD_REMOVAL",),
                status="DRIFT"):
    raw = f"{source} {vendor} {minutes_before_end} {added} {removed} {generate_event_id()}"
    return Event(event_id=generate_event_id(), raw_event=raw, raw_hash=sha256_hex(raw),
                 received_at=END - timedelta(minutes=minutes_before_end), format_detected="kv", status="UNDER_REVIEW",
                 adapter_id=source, vendor=vendor, extensions={}, warnings=[],
                 processing_metadata={"drift": {"status": status, "source_key": source, "change_types": list(change_types),
                                                "differences": {"added_fields": list(added),
                                                                "removed_fields": list(removed)}}})


def finding(source, field, metric="PSI", layer="STATISTICAL"):
    fid = hashlib.sha256(f"{layer}|{source}|{field}|{metric}".encode()).hexdigest()
    return DriftFinding(id=fid, layer=layer, source_key=source, field=field, metric=metric,
                        baseline_start=END - timedelta(hours=169), baseline_end=END - timedelta(hours=1),
                        current_start=END - timedelta(hours=1), current_end=END, baseline_value={}, current_value={},
                        deviation=0.6, threshold=0.25, baseline_n=400, current_n=300, quality="LOW",
                        advisory=layer == "SEMANTIC", explanation="test finding", status="OPEN")


def insert(*rows):
    with TestSessionLocal() as db:
        db.add_all(rows)
        db.commit()


def analyze(client, headers=None, **body):
    r = client.post(f"{API}/drift/correlations/analyze", json={"window_end": END.isoformat(), **body}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def snapshot():
    with TestSessionLocal() as db:
        return {t: db.execute(text(f"SELECT md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM {t} x"))
                .scalar() for t in UNTOUCHED}


def test_two_vendors_with_the_same_drift_correlate(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]))
    [c] = analyze(client)["correlations"]
    assert c["sources"] == ["fw_a", "fw_b"] and c["vendors"] == ["VendorA", "VendorB"]
    assert c["affected_fields"] == ["severity"] and c["change_types"] == ["FIELD_REMOVAL"]
    b = c["score_breakdown"]
    assert b["shared_fields"]["value"] == 1.0 and b["change_overlap"]["value"] == 1.0
    assert b["source_diversity"]["value"] == round(1 / 3, 4)
    assert c["score"] == round(sum(v["contribution"] for v in b.values()), 4) and c["strength"] == "HIGH"
    assert c["investigation_only"] is True and len(c["event_ids"]) == 2


def test_three_vendors_increase_the_source_diversity_contribution(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]))
    two = analyze(client)["correlations"][0]
    insert(drift_event("fw_c", "VendorC", removed=["severity"]))
    [three] = analyze(client)["correlations"]
    assert len(three["vendors"]) == 3
    assert three["score_breakdown"]["source_diversity"]["contribution"] > two["score_breakdown"]["source_diversity"]["contribution"]
    assert three["score"] > two["score"]


def test_unrelated_fields_lower_the_overlap(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]))
    same = analyze(client)["correlations"][0]
    with TestSessionLocal() as db:
        db.execute(text("DELETE FROM events"))
        db.execute(text("DELETE FROM drift_correlations"))
        db.commit()
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["event_action"]))
    [other] = analyze(client)["correlations"]  # linked only by the shared change type
    assert other["score_breakdown"]["shared_fields"]["value"] == 0.0 and other["affected_fields"] == []
    assert other["score"] < same["score"] and other["strength"] != "HIGH"


def test_nothing_in_common_is_not_correlated(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"], change_types=["FIELD_REMOVAL"]),
           drift_event("fw_b", "VendorB", added=["event_action"], change_types=["FIELD_ADDITION"]))
    assert analyze(client)["correlations"] == []


def test_same_vendor_twice_is_not_cross_vendor(client):
    insert(drift_event("fw_a1", "VendorA", removed=["severity"]), drift_event("fw_a2", "VendorA", removed=["severity"]))
    report = analyze(client)
    assert report["sources_with_drift"] == 2 and report["correlations"] == []


def test_outside_the_window_does_not_correlate(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]),
           drift_event("fw_b", "VendorB", removed=["severity"], minutes_before_end=90))
    assert analyze(client)["correlations"] == []
    assert len(analyze(client, window_minutes=120)["correlations"]) == 1


def test_a_single_source_is_never_a_correlation(client):
    insert(*[drift_event("fw_a", "VendorA", removed=["severity"], minutes_before_end=m) for m in (5, 10, 20)])
    assert analyze(client)["correlations"] == []


def test_structural_and_statistical_findings_correlate(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), finding("ids_b", "severity"))
    with TestSessionLocal() as db:  # the statistical source's vendor comes from its events
        db.add(Event(event_id=generate_event_id(), raw_event="x", raw_hash=sha256_hex("x"), received_at=END,
                     format_detected="kv", status="SUCCESS", adapter_id="ids_b", vendor="VendorB", extensions={},
                     processing_metadata={}, warnings=[]))
        db.commit()
    [c] = analyze(client)["correlations"]
    assert c["drift_types"] == ["STATISTICAL", "STRUCTURAL"] and c["affected_fields"] == ["severity"]
    assert c["drift_finding_ids"] == [finding("ids_b", "severity").id] and len(c["event_ids"]) == 1
    assert c["change_types"] == ["FIELD_REMOVAL", "PSI"]


def test_explanation_carries_the_exact_evidence(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]))
    [c] = analyze(client)["correlations"]
    text_ = c["explanation"]
    for fragment in ("2 vendors (VendorA, VendorB)", "fw_a, fw_b", "['severity']", "FIELD_REMOVAL", str(c["score"]),
                     c["strength"], "Investigation aid only"):
        assert fragment in text_, fragment
    assert c["per_source"]["fw_a"]["fields"] == ["severity"] and c["per_source"]["fw_b"]["evidence"] == 1


def test_repeated_analysis_is_deterministic_and_idempotent(client):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]),
           finding("fw_c", "severity"))
    first, second = analyze(client), analyze(client)
    strip = lambda r: [{k: v for k, v in c.items() if k != "created_at"} for c in r["correlations"]]  # noqa: E731
    assert strip(first) == strip(second)
    stored = client.get(f"{API}/drift/correlations").json()["items"]
    assert [c["id"] for c in stored] == [c["id"] for c in first["correlations"]]
    assert client.get(f"{API}/drift/correlations/{stored[0]['id']}").json()["score"] == first["correlations"][0]["score"]
    assert client.get(f"{API}/drift/correlations/nope").status_code == 404


def test_analysis_never_mutates_production_state(client, users):
    insert(drift_event("fw_a", "VendorA", removed=["severity"]), drift_event("fw_b", "VendorB", removed=["severity"]),
           finding("fw_c", "severity"))
    before = snapshot()
    report = analyze(client, users["analyst"])
    assert report["correlations"] and snapshot() == before
    entry = client.get(f"{API}/governance/audit", params={"action": "DRIFT_CORRELATION_ANALYZE"},
                       headers=users["admin"]).json()["items"][0]
    assert entry["decision"] == "SUCCESS" and entry["actor"] == "analyst"
    assert entry["details"]["correlations"] == [c["id"] for c in report["correlations"]]


def test_strength_categories():
    assert svc.strength(0.9) == "HIGH" and svc.strength(0.5) == "MEDIUM" and svc.strength(0.1) == "LOW"
    assert abs(sum(svc.WEIGHTS.values()) - 1.0) < 1e-9
