"""Drift intelligence end to end (full API + real Postgres): classification,
severity, critical fields, explanation, recommendations, adaptive variants,
structural history and possible format drift (adapter fallback)."""
import json

import pytest

from app.adapters.loader import get_adapter_registry
from app.config import get_settings
from app.schema.adapter import AdapterMapping
from tests.conftest import load_fixture
from tests.integration.test_drift_flow import PA_DRIFTED, PA_SAME_STRUCTURE

PA_THIRD = (
    "<134>Jan 19 09:00:00 PA-VM CEF:0|Palo Alto Networks|PAN-OS|12.0.0|traffic|THREAT|5|"
    "src=10.0.0.30 dst=93.184.216.34 spt=51500 dpt=443 proto=tcp sessionid=777 app=ssl rule=allow-web"
)
# The same PA event with the critical source-IP field (src -> network.src_ip) missing.
PA_NO_SRC = PA_SAME_STRUCTURE.replace("src=10.9.9.9 ", "")

ACME_BASE = {"vendor": "ACME Corp", "action": "allow", "src_ip": "10.0.0.1", "dst_port": "443", "session_id": "s1", "bytes": 10}


def ingest(client, raw: str) -> dict:
    resp = client.post("/api/v1/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    return resp.json()


def acme(**changes) -> str:
    event = {**ACME_BASE, **changes}
    return json.dumps({k: v for k, v in event.items() if v is not ...})


def drift_of(body: dict) -> dict:
    return body["processing_metadata"].get("drift") or {}


def accept(client, event_id: str, mode: str, note: str | None = None):
    return client.post(f"/api/v1/events/{event_id}/drift/accept", json={"mode": mode, "note": note})


@pytest.fixture()
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "drift_enabled", True)
    monkeypatch.setattr(s, "drift_similarity_threshold", 0.85)
    return s


@pytest.fixture()
def pa_baseline(client, settings):
    return ingest(client, load_fixture("cef_samples/paloalto.txt"))


@pytest.fixture()
def acme_source(client, settings, monkeypatch):
    """A JSON vendor adapter (typed values) injected into the in-memory
    registry for this test only, with a vendor-specific critical field."""
    registry = get_adapter_registry()
    adapter = AdapterMapping.model_validate(
        {
            "id": "acme_json",
            "vendor": "ACME",
            "product": "Gateway",
            "format": "json",
            "match": {"format": "json", "field": "vendor", "equals": "ACME Corp"},
            "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
            "event_action_field": "action",
            "field_map": {"src_ip": {"target": "network.src_ip"}, "dst_port": {"target": "network.dst_port", "type": "int"}},
            "critical_fields": ["session_id"],
        }
    )
    monkeypatch.setattr(registry, "_adapters", [adapter, *registry.all()])
    body = ingest(client, acme())
    assert body["adapter_id"] == "acme_json"
    assert drift_of(body)["status"] == "BASELINE_CREATED"
    return body


# --- A: identical ---------------------------------------------------------------------------


def test_identical_structure_has_no_change_report(client, pa_baseline):
    d = drift_of(ingest(client, PA_SAME_STRUCTURE))
    assert d["status"] == "NORMAL"
    assert d["change_types"] == []
    assert d["severity"] is None
    assert d["recommended_actions"] == []
    assert d["explanation"].startswith("NO DRIFT (NORMAL)\nSource: paloalto_cef")


# --- B/C/F: additions, removals, multiple -----------------------------------------------------


def test_added_field_below_threshold_is_classified_and_graded(client, pa_baseline, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_similarity_threshold", 1.0)
    d = drift_of(ingest(client, PA_SAME_STRUCTURE + " cn1=1"))
    assert d["status"] == "DRIFT"
    assert d["change_types"] == ["FIELD_ADDITION"]
    assert d["decision_reasons"] == ["SIMILARITY_BELOW_THRESHOLD"]
    assert d["severity"] == "LOW"
    assert d["recommended_actions"] == ["REVIEW_ADDED_FIELDS"]


def test_tolerated_addition_is_normal_but_reported(client, pa_baseline):
    d = drift_of(ingest(client, PA_SAME_STRUCTURE + " cn1=1"))
    assert d["status"] == "NORMAL"
    assert d["change_types"] == ["FIELD_ADDITION"]
    assert d["differences"]["added_fields"] == ["cn1"]


def test_multiple_simultaneous_changes(client, pa_baseline):
    body = ingest(client, PA_DRIFTED)
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW"
    assert d["change_types"] == ["FIELD_ADDITION", "FIELD_REMOVAL", "MULTIPLE_STRUCTURAL_CHANGE"]
    assert d["decision_reasons"] == ["SIMILARITY_BELOW_THRESHOLD"]
    # 5 added x1 + 3 removed x2 + magnitude floor(10 x (1 - 0.6364)) = 3  -> 14 -> HIGH
    assert d["severity_factors"] == {"added_fields": 5, "removed_fields": 6, "structural_magnitude": 3}
    assert (d["severity"], d["severity_score"]) == ("HIGH", 14)
    assert d["critical_field_changes"] == []
    assert d["recommended_actions"] == ["REVIEW_REMOVED_FIELDS", "REVIEW_ADDED_FIELDS"]
    assert d["reonboarding_required"] is True


# --- G: critical field removal ---------------------------------------------------------------


def test_critical_field_removal_forces_review_despite_high_similarity(client, pa_baseline):
    body = ingest(client, PA_NO_SRC)
    d = drift_of(body)
    assert d["similarity"] >= d["threshold"]  # would be NORMAL on similarity alone
    assert body["status"] == "UNDER_REVIEW"
    assert d["status"] == "DRIFT"
    assert d["decision_reasons"] == ["CRITICAL_FIELD_CHANGED"]
    assert d["critical_field_changes"] == [
        {"field": "src", "target": "network.src_ip", "change": "removed", "baseline_type": None, "current_type": None}
    ]
    assert d["severity"] == "HIGH"
    assert d["recommended_actions"][0] == "REVIEW_CRITICAL_FIELD_CHANGE"
    assert d["reonboarding_required"] is True
    assert "! critical field removed: src (network.src_ip)" in d["explanation"]
    # raw and normalized output preserved
    assert body["raw_event"] == PA_NO_SRC
    assert body["normalized_event"]["vendor"] == "Palo Alto Networks"


def test_vendor_specific_critical_field_from_adapter_yaml(client, acme_source):
    body = ingest(client, acme(session_id=...))
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW"
    assert d["critical_field_changes"][0]["field"] == "session_id"
    assert d["critical_field_changes"][0]["target"] is None


# --- D/H: type changes -------------------------------------------------------------------------


def test_critical_field_type_change(client, acme_source):
    body = ingest(client, acme(dst_port=443))
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW"
    assert d["change_types"] == ["FIELD_TYPE_CHANGE"]
    assert d["differences"]["type_changes"] == {"dst_port": {"baseline": "string", "current": "integer"}}
    assert d["critical_field_changes"] == [
        {"field": "dst_port", "target": "network.dst_port", "change": "type_changed",
         "baseline_type": "string", "current_type": "integer"}
    ]
    assert (d["severity"], d["severity_score"]) == ("HIGH", 10)  # 4 type + 6 critical
    assert d["recommended_actions"] == ["REVIEW_CRITICAL_FIELD_CHANGE", "REVIEW_TYPE_CHANGES"]
    assert "~ dst_port type changed string → integer" in d["explanation"]
    assert body["network"]["dst_port"] == 443  # normalization unaffected


def test_non_critical_type_change_is_tolerated_but_reported(client, acme_source):
    d = drift_of(ingest(client, acme(bytes="10")))
    assert d["status"] == "NORMAL"
    assert d["change_types"] == ["FIELD_TYPE_CHANGE"]


def test_nested_object_type_change(client, acme_source, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_similarity_threshold", 0.99)
    d = drift_of(ingest(client, acme(bytes={"in": 5, "out": 5})))
    assert d["status"] == "DRIFT"
    assert d["differences"]["type_changes"] == {"bytes": {"baseline": "integer", "current": "object"}}


# --- E: reorder ------------------------------------------------------------------------------------


def test_reorder_is_classified(client, acme_source):
    reversed_event = json.dumps(dict(reversed(list(ACME_BASE.items()))))
    body = ingest(client, reversed_event)
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW"
    assert d["change_types"] == ["FIELD_ORDER_CHANGE"]
    assert d["severity"] == "LOW"
    assert d["recommended_actions"] == ["REVIEW_FIELD_ORDER"]
    assert "↕ field order changed" in d["explanation"]


# --- N/O: explanation + recommendations through GET /events/{id} ------------------------------------


def test_explanation_exposed_on_get_event(client, pa_baseline):
    event_id = ingest(client, PA_DRIFTED)["event_id"]
    d = drift_of(client.get(f"/api/v1/events/{event_id}").json())
    text = d["explanation"]
    assert text.startswith("DRIFT DETECTED\nSource: paloalto_cef\nBaseline Version: 1 (auto_bootstrap)")
    assert "Similarity: 0.7451" in text and "Threshold: 0.85" in text
    assert "Severity: HIGH (score 14)" in text
    for line in ("+ cn1", "+ deviceExternalId", "- suser", "- cs1Label"):
        assert line in text.split("\n")
    assert text.endswith("Recommendation:\nREVIEW_REMOVED_FIELDS, REVIEW_ADDED_FIELDS\nHuman review required.")

    accept(client, event_id, "add_variant")
    text = drift_of(client.get(f"/api/v1/events/{event_id}").json())["explanation"]
    assert "Reviewed: accepted_variant" in text and "Human review required." not in text


# --- J/K/L: adaptive variants + history ---------------------------------------------------------------


def test_approved_variant_match_is_normal_and_leaves_baseline_untouched(client, pa_baseline):
    first = ingest(client, PA_DRIFTED)
    accept(client, first["event_id"], "add_variant")
    before = client.get("/api/v1/drift/baselines/paloalto_cef").json()

    for n in range(3):
        body = ingest(client, PA_DRIFTED.replace("cn1=42", f"cn1={n}"))
        d = drift_of(body)
        assert body["status"] == "SUCCESS"
        assert d["status"] == "NORMAL" and d["matched"] == "variant:0"
        assert d["change_types"] == [] and d.get("severity") is None
    ref = drift_of(ingest(client, PA_SAME_STRUCTURE))
    assert ref["matched"] == "reference"

    after = client.get("/api/v1/drift/baselines/paloalto_cef").json()
    assert after["version"] == before["version"] == 2
    assert after["history"] == before["history"]
    assert after["under_review_count"] == 0


def test_duplicate_variants_never_added_including_reference_duplicates(client, pa_baseline):
    a = ingest(client, PA_DRIFTED)
    b = ingest(client, PA_DRIFTED)
    c = ingest(client, PA_THIRD)
    c2 = ingest(client, PA_THIRD)
    accept(client, a["event_id"], "add_variant")
    resp = accept(client, b["event_id"], "add_variant")
    assert resp.json()["baseline"]["version"] == 2
    assert len(resp.json()["baseline"]["accepted_variants"]) == 1

    accept(client, c["event_id"], "replace_baseline")  # PA_THIRD becomes the reference (v3)
    resp = accept(client, c2["event_id"], "add_variant")  # identical to the new reference
    baseline = resp.json()["baseline"]
    assert resp.status_code == 200 and resp.json()["event"]["status"] == "SUCCESS"
    assert baseline["version"] == 3
    assert baseline["accepted_variants"] == []
    assert [h["action"] for h in baseline["history"]] == ["BASELINE_CREATED", "VARIANT_ADDED", "BASELINE_REPLACED"]


def test_baseline_version_evolution_history(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    third = ingest(client, PA_THIRD)

    v2 = accept(client, drifted["event_id"], "add_variant", "PAN-OS 11").json()["baseline"]
    assert v2["accepted_variants"][0]["accepted_in_version"] == 2

    v3 = accept(client, third["event_id"], "replace_baseline", "PAN-OS 12 rollout").json()["baseline"]
    assert v3["version"] == 3 and v3["origin"] == "human_review"

    history = client.get("/api/v1/drift/baselines/paloalto_cef").json()["history"]
    assert [(h["version"], h["action"]) for h in history] == [
        (1, "BASELINE_CREATED"),
        (2, "VARIANT_ADDED"),
        (3, "BASELINE_REPLACED"),
    ]
    h1, h2, h3 = history
    assert h1["event_id"] == pa_baseline["event_id"] and h1["field_count"] == 17 and h1["changes"] is None
    assert h2["event_id"] == drifted["event_id"] and h2["note"] == "PAN-OS 11"
    assert h2["changes"]["added_fields"] == ["cn1", "cs2", "cs2Label", "deviceExternalId", "rt"]
    assert h2["changes"]["removed_fields"] == ["cs1", "cs1Label", "suser"]
    assert "MULTIPLE_STRUCTURAL_CHANGE" in h2["changes"]["change_types"]
    # v3 delta is relative to the v1 reference in force before the replacement
    assert h3["changes"]["added_fields"] == ["app", "rule", "sessionid"]
    assert h3["signature"] == third["structural_fingerprint"]["signature"]

    # list view stays light: no history there
    assert client.get("/api/v1/drift/baselines").json()["items"][0]["history"] is None


def test_acknowledge_restores_status_without_touching_baseline(client, pa_baseline):
    drifted = ingest(client, PA_DRIFTED)
    resp = accept(client, drifted["event_id"], "acknowledge", "known, adapter fix pending")
    assert resp.status_code == 200
    assert resp.json()["event"]["status"] == "SUCCESS"
    assert drift_of(resp.json()["event"])["review"]["resolution"] == "acknowledged"
    assert resp.json()["baseline"]["version"] == 1
    assert len(resp.json()["baseline"]["history"]) == 1


# --- M: possible format drift (adapter fallback) ---------------------------------------------------------


FORTI_RENAMED = load_fixture("syslog_samples/fortinet.txt").replace("FORTIGATE:", "FortiGate:")
PA_AS_JSON = json.dumps(
    {"device_vendor": "Palo Alto Networks", "src": "10.0.0.30", "dst": "93.184.216.34", "dpt": 443, "act": "allow"}
)


def test_vendor_near_miss_is_possible_format_drift(client, settings):
    ingest(client, load_fixture("syslog_samples/fortinet.txt"))  # fortinet becomes a known source
    body = ingest(client, FORTI_RENAMED)

    assert body["adapter_id"] == "syslog_generic"  # adapter behavior unchanged: it no longer matches
    assert body["status"] == "UNDER_REVIEW"
    assert body["raw_event"] == FORTI_RENAMED
    assert body["normalized_event"] is not None
    d = drift_of(body)
    assert d["status"] == "POSSIBLE_FORMAT_DRIFT"
    assert d["source_key"] == "fortinet"
    assert d["current_adapter"] == "syslog_generic"
    assert [e["reason"] for e in d["evidence"]] == ["MATCH_FIELD_NEAR_MISS", "VENDOR_SIGNATURE_IN_RAW"]
    assert "observed 'FortiGate'" in d["evidence"][0]["detail"]
    assert d["decision_reasons"] == ["ADAPTER_FALLBACK"]
    assert "FORMAT_DRIFT" in d["change_types"]
    assert (d["severity"], d["severity_score"]) == ("HIGH", 9)
    assert d["recommended_actions"][0] == "REVIEW_FORMAT_DRIFT"
    assert d["reonboarding_required"] is True
    assert d["original_status"] == "SUCCESS"
    assert d["explanation"].startswith(
        "POSSIBLE FORMAT DRIFT\nPreviously known source: fortinet\nCurrent adapter: syslog_generic"
    )
    # counted toward the vendor source's review queue
    assert client.get("/api/v1/drift/baselines/fortinet").json()["under_review_count"] == 1


def test_vendor_identity_in_other_format_is_critical(client, pa_baseline):
    body = ingest(client, PA_AS_JSON)
    d = drift_of(body)
    assert body["adapter_id"] == "json_generic"
    assert d["status"] == "POSSIBLE_FORMAT_DRIFT" and d["source_key"] == "paloalto_cef"
    assert [e["reason"] for e in d["evidence"]] == ["VENDOR_IDENTITY_FIELD_IN_OTHER_FORMAT", "VENDOR_SIGNATURE_IN_RAW"]
    assert d["decision_reasons"] == ["ADAPTER_FALLBACK", "FORMAT_CHANGED"]
    assert d["differences"]["format_changed"] == {"baseline": "cef", "current": "json"}
    assert d["severity"] == "CRITICAL"


def test_unrelated_generic_event_is_not_flagged(client, pa_baseline):
    ingest(client, load_fixture("syslog_samples/fortinet.txt"))
    for raw in (load_fixture("json_samples/valid_login.json"), load_fixture("syslog_samples/rfc5424_valid.txt"),
                load_fixture("cef_samples/valid_generic.txt")):
        body = ingest(client, raw)
        assert body["status"] == "SUCCESS"
        assert body["processing_metadata"].get("drift") is None


def test_no_format_drift_without_a_known_source(client, settings):
    body = ingest(client, FORTI_RENAMED)  # fortinet never seen -> nothing to drift from
    assert body["status"] == "SUCCESS"
    assert body["processing_metadata"].get("drift") is None


def test_possible_format_drift_only_allows_acknowledge(client, settings):
    ingest(client, load_fixture("syslog_samples/fortinet.txt"))
    event_id = ingest(client, FORTI_RENAMED)["event_id"]
    for mode in ("add_variant", "replace_baseline"):
        resp = accept(client, event_id, mode)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "CONFLICT"
    resp = accept(client, event_id, "acknowledge")
    assert resp.status_code == 200
    assert resp.json()["event"]["status"] == "SUCCESS"
    assert resp.json()["baseline"]["version"] == 1  # baseline never changed by format drift


# --- P: DRIFT_ENABLED=false ------------------------------------------------------------------------------


def test_drift_disabled_disables_all_intelligence(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_enabled", False)
    ingest(client, load_fixture("syslog_samples/fortinet.txt"))
    for raw in (FORTI_RENAMED, PA_NO_SRC):
        body = ingest(client, raw)
        assert body["status"] == "SUCCESS"
        assert body["processing_metadata"].get("drift") is None
    assert client.get("/api/v1/drift/baselines").json()["total"] == 0


# --- failure isolation for the new fallback path ----------------------------------------------------------


def test_fallback_evaluation_error_never_fails_the_event(client, settings, monkeypatch):
    from app.pipeline.drift import analysis

    ingest(client, load_fixture("syslog_samples/fortinet.txt"))

    def boom(*args, **kwargs):
        raise RuntimeError("simulated evidence bug")

    monkeypatch.setattr(analysis, "fallback_evidence", boom)
    body = ingest(client, FORTI_RENAMED)
    assert body["status"] == "SUCCESS"
    assert body["raw_event"] == FORTI_RENAMED
    assert drift_of(body)["status"] == "ERROR"
