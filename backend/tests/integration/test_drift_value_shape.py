"""Critical-field value-shape drift (E2E defect D3): a critical field whose value no longer fits its
typed target (`spt=not-a-port`, `src=10.0.0.999`, an unparseable timestamp) used to stay NORMAL
because text parsers type every value as a string. Ordinary value changes must stay NORMAL."""
import json

import pytest

from app.adapters.loader import get_adapter_registry
from app.config import get_settings
from app.schema.adapter import AdapterMapping
from tests.conftest import load_fixture
from tests.integration.test_learning_flow import base_log

API = "/api/v1"
PA = ("<134>Jan 19 08:00:00 PA-VM2 CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|3|"
      "src={src} dst=1.1.1.1 spt={spt} dpt=80 proto=udp act={act} suser={user} cs1Label=URL cs1=example.org")


def pa(src="10.9.9.9", spt="40000", act="deny", user="asmith"):
    return PA.format(src=src, spt=spt, act=act, user=user)


def ingest(client, raw: str) -> dict:
    resp = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert resp.status_code == 201, resp.text
    return resp.json()


def drift_of(body: dict) -> dict:
    return body["processing_metadata"].get("drift") or {}


def accept(client, event_id, mode, note="reviewed"):
    return client.post(f"{API}/events/{event_id}/drift/accept", json={"mode": mode, "note": note})


@pytest.fixture()
def settings(monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85),
                        ("drift_value_shape_enabled", True), ("anthropic_api_key", "")):
        monkeypatch.setattr(s, name, value)
    return s


@pytest.fixture()
def pa_source(client, settings):
    body = ingest(client, pa())
    assert drift_of(body)["status"] == "BASELINE_CREATED"
    return body


# 1 + 5: ordinary value changes are never findings ----------------------------------------------


def test_1_valid_port_to_another_valid_port_is_normal(client, pa_source):
    for spt in ("1", "443", "65535"):
        d = drift_of(ingest(client, pa(spt=spt)))
        assert d["status"] == "NORMAL" and "FIELD_VALUE_SHAPE_CHANGE" not in (d.get("change_types") or [])


def test_5_string_to_different_string_is_normal(client, pa_source):
    body = ingest(client, pa(act="allow", user="someone.else", src="192.0.2.77"))
    assert body["status"] == "SUCCESS" and drift_of(body)["status"] == "NORMAL"


# 2: numeric -> non-numeric ---------------------------------------------------------------------


def test_2_port_becomes_non_numeric_forces_review(client, pa_source):
    raw = pa(spt="not-a-port")
    body = ingest(client, raw)
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW" and d["status"] == "DRIFT"
    assert d["similarity"] == 1.0  # structure is identical: this is value-shape drift only
    assert d["decision_reasons"] == ["CRITICAL_FIELD_VALUE_SHAPE_CHANGED"]
    assert d["change_types"] == ["FIELD_VALUE_SHAPE_CHANGE"]
    assert d["critical_field_changes"] == [{"field": "spt", "target": "network.src_port", "change": "type_changed",
                                            "baseline_type": "port", "current_type": "string"}]
    [finding] = d["value_shape_changes"]
    assert finding["expected_shape"] == "port" and finding["observed_shape"] == "string"
    assert finding["value_preview"] == "not-a-port"
    assert d["severity"] == "HIGH" and d["recommended_actions"][0] == "REVIEW_CRITICAL_FIELD_CHANGE"
    assert d["original_status"] == "PARTIAL"  # typed coercion already failed -> PARTIAL, now also reviewed
    assert "critical field value no longer fits its type: spt (network.src_port) expected port" in d["explanation"]
    assert body["raw_event"] == raw and body["extensions"]["spt"] == "not-a-port"  # nothing lost


def test_2b_out_of_range_port_forces_review(client, pa_source):
    d = drift_of(ingest(client, pa(spt="70000")))
    assert d["status"] == "DRIFT" and d["value_shape_changes"][0]["observed_shape"] == "integer_out_of_port_range"


# 3: IP -> malformed IP -------------------------------------------------------------------------


def test_3_ip_becomes_malformed_forces_review(client, pa_source):
    body = ingest(client, pa(src="10.0.0.999"))
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW"
    assert d["critical_field_changes"] == [{"field": "src", "target": "network.src_ip", "change": "type_changed",
                                            "baseline_type": "ip", "current_type": "string"}]


def test_3b_ipv6_is_a_valid_ip(client, pa_source):
    assert drift_of(ingest(client, pa(src="2001:db8::7")))["status"] == "NORMAL"


# 4: timestamp -> malformed timestamp (only when the timestamp target is declared critical) --------


@pytest.fixture()
def ts_source(client, settings, monkeypatch):
    registry = get_adapter_registry()
    adapter = AdapterMapping.model_validate({
        "id": "tsco_json", "vendor": "TSCO", "product": "Gateway", "format": "json",
        "match": {"format": "json", "field": "vendor", "equals": "TSCO"},
        "ocsf": {"class_uid": 4001, "class_name": "Network Activity", "category_uid": 4, "category_name": "Network Activity"},
        "event_action_field": "action", "timestamp_field": "ts",
        "field_map": {"src_ip": {"target": "network.src_ip"}},
    })
    monkeypatch.setattr(registry, "_adapters", [adapter, *registry.all()])
    monkeypatch.setattr(settings, "drift_critical_fields", settings.drift_critical_fields + ",timestamp")
    body = ingest(client, json.dumps({"vendor": "TSCO", "ts": "2026-09-28T10:00:00Z", "action": "a", "src_ip": "10.0.0.1"}))
    assert drift_of(body)["status"] == "BASELINE_CREATED"


def test_4_timestamp_becomes_unparseable_forces_review(client, ts_source):
    ok = ingest(client, json.dumps({"vendor": "TSCO", "ts": "2026-09-28T11:00:00Z", "action": "b", "src_ip": "10.0.0.2"}))
    assert drift_of(ok)["status"] == "NORMAL"
    bad = ingest(client, json.dumps({"vendor": "TSCO", "ts": "yesterday-ish", "action": "b", "src_ip": "10.0.0.2"}))
    d = drift_of(bad)
    assert bad["status"] == "UNDER_REVIEW"
    assert d["value_shape_changes"][0] | {"reason": None} == {
        "field": "ts", "target": "timestamp", "expected_shape": "timestamp", "observed_shape": "unparseable_timestamp",
        "value_preview": "yesterday-ish", "reason": None}


def test_4b_timestamp_not_checked_unless_declared_critical(client, ts_source, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_critical_fields", "network.src_ip")
    bad = ingest(client, json.dumps({"vendor": "TSCO", "ts": "yesterday-ish", "action": "b", "src_ip": "10.0.0.2"}))
    assert drift_of(bad)["status"] == "NORMAL"


# 6-8: structural drift is unchanged ------------------------------------------------------------


def test_6_7_8_structural_changes_keep_their_phase5_classification(client, pa_source):
    added = drift_of(ingest(client, pa() + " cs2Label=Zone cs2=trust cn1=42 cs3=a cs4=b"))
    assert "FIELD_ADDITION" in added["change_types"] and added["value_shape_changes"] == []
    removed = drift_of(ingest(client, pa().replace("src=10.9.9.9 ", "")))
    assert removed["decision_reasons"] == ["CRITICAL_FIELD_CHANGED"] and not removed.get("value_shape_changes")
    reordered = "<134>Jan 19 08:00:00 PA-VM2 CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|3|" \
                "suser=asmith act=deny proto=udp dpt=80 spt=40000 dst=1.1.1.1 src=10.9.9.9 cs1Label=URL cs1=example.org"
    d = drift_of(ingest(client, reordered))
    assert d["differences"]["order_changed"] is True and "FIELD_VALUE_SHAPE_CHANGE" not in d["change_types"]


# 9: every shipped vendor fixture stays exactly as before ---------------------------------------


@pytest.mark.parametrize("fixture", ["syslog_samples/cisco_asa.txt", "syslog_samples/fortinet.txt", "cef_samples/paloalto.txt"])
def test_9_existing_vendor_logs_have_no_value_shape_findings(client, settings, fixture):
    for line in [x for x in load_fixture(fixture).splitlines() if x.strip()] * 2:
        d = drift_of(ingest(client, line))
        assert d["status"] in ("BASELINE_CREATED", "NORMAL"), d
        assert not d.get("value_shape_changes")


# 10: onboarded (Phase 3, declarative kv) source -------------------------------------------------


@pytest.fixture()
def onboarded(client, settings):
    samples = [base_log(i) for i in range(12)]
    s = client.post(f"{API}/onboarding/sessions", json={"samples": samples}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    r = client.post(f"{API}/onboarding/sessions/{s['id']}/approve",
                    json={"proposal_version": s["proposal_version"], "approved_by": "onboarder"})
    assert r.status_code == 200, r.text
    assert drift_of(ingest(client, base_log(100)))["status"] == "BASELINE_CREATED"


def test_10_onboarded_kv_source(client, onboarded):
    assert drift_of(ingest(client, base_log(101, srcport="51000", srcip="192.0.2.8")))["status"] == "NORMAL"
    body = ingest(client, base_log(102, srcport="NOTANUMBER"))
    d = drift_of(body)
    assert body["status"] == "UNDER_REVIEW" and d["source_key"] == "acmefw_acmefw"
    assert d["value_shape_changes"][0]["field"] == "srcport" and d["value_shape_changes"][0]["target"] == "network.src_port"


# review lifecycle -------------------------------------------------------------------------------


def test_accepting_records_the_shape_so_it_is_normal_afterwards(client, pa_source):
    first = ingest(client, pa(spt="port-x"))
    assert first["status"] == "UNDER_REVIEW"
    v_before = client.get(f"{API}/drift/baselines/paloalto_cef").json()["version"]
    r = accept(client, first["event_id"], "add_variant", "vendor sends a text marker for unknown ports")
    assert r.status_code == 200, r.text
    baseline = client.get(f"{API}/drift/baselines/paloalto_cef").json()
    assert baseline["version"] == v_before + 1  # structure was already known: the shape acceptance is the change
    assert baseline["accepted_value_shapes"] == {"spt": ["string"]}
    last = baseline["history"][-1]
    assert last["action"] == "VARIANT_ADDED" and last["changes"]["accepted_value_shapes"] == {"spt": ["string"]}
    assert drift_of(ingest(client, pa(spt="port-x")))["status"] == "NORMAL"
    # a different bad shape for the same field is still reviewed
    assert drift_of(ingest(client, pa(spt="99999")))["status"] == "DRIFT"


def test_acknowledge_does_not_change_the_baseline(client, pa_source):
    first = ingest(client, pa(spt="port-x"))
    v = client.get(f"{API}/drift/baselines/paloalto_cef").json()["version"]
    assert accept(client, first["event_id"], "acknowledge").status_code == 200
    baseline = client.get(f"{API}/drift/baselines/paloalto_cef").json()
    assert baseline["version"] == v and baseline["accepted_value_shapes"] == {}
    assert drift_of(ingest(client, pa(spt="port-x")))["status"] == "DRIFT"


def test_replace_baseline_keeps_the_reviewed_shape(client, pa_source):
    first = ingest(client, pa(spt="port-x"))
    assert accept(client, first["event_id"], "replace_baseline").status_code == 200
    assert client.get(f"{API}/drift/baselines/paloalto_cef").json()["accepted_value_shapes"] == {"spt": ["string"]}
    assert drift_of(ingest(client, pa(spt="port-x")))["status"] == "NORMAL"


def test_disabled_flag_restores_structural_only_behavior(client, pa_source, settings, monkeypatch):
    monkeypatch.setattr(settings, "drift_value_shape_enabled", False)
    body = ingest(client, pa(spt="not-a-port"))
    assert drift_of(body)["status"] == "NORMAL" and body["status"] == "PARTIAL"


def test_reprocess_reevaluates_value_shape(client, pa_source):
    body = ingest(client, pa(spt="not-a-port"))
    again = client.post(f"{API}/events/{body['event_id']}/reprocess").json()["event"]
    assert again["status"] == "UNDER_REVIEW" and drift_of(again)["value_shape_changes"][0]["field"] == "spt"
    assert again["raw_hash"] == body["raw_hash"]


def test_vendor_placeholders_mean_no_value_not_shape_drift(client, pa_source):
    for marker in ("-", "n/a", "NONE", "unknown"):
        body = ingest(client, pa(spt=marker))
        assert drift_of(body)["status"] == "NORMAL" and body["status"] == "PARTIAL", marker  # value kept in extensions


def test_bootstrap_event_with_an_invalid_value_does_not_drift_against_its_own_baseline(client, settings):
    first = ingest(client, pa(spt="abobject"))
    assert drift_of(first)["status"] == "BASELINE_CREATED" and first["status"] == "PARTIAL"
    baseline = client.get(f"{API}/drift/baselines/paloalto_cef").json()
    assert baseline["accepted_value_shapes"] == {"spt": ["string"]} and baseline["history"][0]["action"] == "BASELINE_CREATED"
    again = client.post(f"{API}/events/{first['event_id']}/reprocess").json()["event"]
    assert again["status"] == "PARTIAL" and drift_of(again)["status"] == "NORMAL"
    assert drift_of(ingest(client, pa(src="10.0.0.999")))["status"] == "DRIFT"  # other fields still checked
