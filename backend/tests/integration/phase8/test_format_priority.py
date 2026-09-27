"""Phase 8 Step 2: native LEEF/XML must never change existing behavior.

The golden file was captured from the pre-Step-2 code (see format_scenarios).
Only one behavior is allowed to change: a valid bare LEEF/XML event that was
FAILED/unknown because nothing claimed it is now parsed natively.
"""
import json

import pytest

from app.pipeline.detector.format_detector import detect_format, detect_secondary_format
from app.pipeline.orchestrator import process
from app.pipeline.parsers.registry import get_parser
from app.schema.ocsf import FormatType
from app.services import drift_service
from tests.integration.phase7.conftest import API, _phase7_defaults, ingest, isolated_stores  # noqa: F401
from tests.integration.phase8 import format_scenarios as sc

GOLDEN = json.loads(sc.GOLDEN.read_text(encoding="utf-8"))
CORPUS = {name: (kind, raw) for name, kind, raw in GOLDEN["corpus"]}
INFORMATIONAL = {"parsed_samples", "samples"}


@pytest.fixture(scope="module")
def current():
    return sc.run_pipeline()


def test_golden_corpus_matches_current_scenarios():
    assert [[n, k, r] for n, k, r in sc.corpus()] == GOLDEN["corpus"]  # same inputs as captured


# --- TEST 10 (+ the core of 1-3): nothing that existed changes -------------------------------------------


def test_existing_formats_and_adapters_are_byte_identical(current):
    changed = []
    for registry, results in GOLDEN["pipeline"].items():
        for name, before in results.items():
            kind, _ = CORPUS[name]
            after = current[registry][name]
            if after == before:
                continue
            native_takeover = (kind == "native" and before["format_detected"] == "unknown" and before["status"] == "FAILED"
                               and before["adapter_id"] is None and after["format_detected"] in ("leef", "xml"))
            if not native_takeover:
                changed.append((registry, name, before["format_detected"], after["format_detected"]))
    assert changed == []


def test_only_unclaimed_bare_leef_xml_changed(current):
    newly_native = sorted((reg, name) for reg, res in GOLDEN["pipeline"].items() for name, before in res.items()
                          if current[reg][name] != before)
    assert newly_native, "expected some bare LEEF/XML to become native"
    for reg, name in newly_native:
        assert CORPUS[name][0] == "native"
        assert GOLDEN["pipeline"][reg][name]["status"] == "FAILED"
        assert current[reg][name]["adapter_id"] in ("leef_generic", "xml_generic")


# --- TEST 1: onboarded adapters keep priority ---------------------------------------------------------------


@pytest.mark.parametrize("registry,prefix,adapter", [
    ("leef2_delimited", "leef2_source#", "golden_leef2_delimited"),
    ("leef1_kv", "leef1_source#", "golden_leef1_kv"),
    ("leef1_kv", "bare_leef1", "golden_leef1_kv"),  # a different vendor's bare LEEF also claimed by the kv adapter
])
def test_onboarded_adapter_wins_over_native_leef(current, registry, prefix, adapter):
    names = [n for n in CORPUS if n.startswith(prefix)]
    assert names
    for name in names:
        assert current[registry][name]["adapter_id"] == adapter
        assert current[registry][name] == GOLDEN["pipeline"][registry][name]  # byte-equivalent to pre-Phase-8


# A human proposal that passes the Phase 3 gates (>= 30% mapping coverage). The
# golden scenario's proposal is intentionally left unchanged (it was captured pre-change).
LEEF2_E2E_PROPOSAL = {**sc.LEEF2_HUMAN_PROPOSAL, "mappings": sc.LEEF2_HUMAN_PROPOSAL["mappings"] + [
    {"raw_field": "col_7", "target": "raw_message", "confidence": 0.8, "evidence": "attribute payload column"}]}


def test_onboarded_delimited_leef2_source_end_to_end(client):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": sc.LEEF2, "name": "leef2 source"}).json()
    s = client.put(f"{API}/onboarding/sessions/{s['id']}/proposal", json={"proposal": LEEF2_E2E_PROPOSAL}).json()
    assert s["validation"]["result"] == "PASSED", s["validation"]["reasons"]
    approved = client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    assert approved.status_code == 200
    e = ingest(client, sc.LEEF2[0])
    assert e["format_detected"] == "delimited" and e["adapter_id"] == approved.json()["adapter"]["adapter_id"]
    other = ingest(client, sc.BARE_LEEF2)  # a LEEF 2.0 line the onboarded adapter does not match
    assert other["format_detected"] == "leef" and other["adapter_id"] == "leef_generic"


def test_onboarded_kv_leef1_source_end_to_end(client):
    s = client.post(f"{API}/onboarding/sessions", json={"samples": sc.LEEF1}).json()
    s = client.post(f"{API}/onboarding/sessions/{s['id']}/suggest", json={"provider": "offline"}).json()
    assert s["validation"]["result"] == "PASSED"
    client.post(f"{API}/onboarding/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"]})
    e = ingest(client, sc.LEEF1[3])
    assert e["format_detected"] == "kv" and e["processing_metadata"]["adapter_source"] == "onboarded"


# --- TEST 2: a kv log that resembles LEEF is not hijacked -----------------------------------------------------


@pytest.mark.parametrize("raw", [sc.KV_LIKE[0], "vendor=Acme msg=LEEF:1.0|IBM|QRadar|7.4|Login| src=1.1.1.1",
                                 "LEEF:3.0|Acme|Gateway|2.1|Deny|src=1.1.1.1", "LEEF:1.0|Acme|Gateway"])
def test_leef_lookalikes_are_not_classified_as_leef(client, raw):
    assert detect_secondary_format(raw) == FormatType.UNKNOWN
    e = ingest(client, raw)
    assert e["format_detected"] == "unknown" and e["status"] == "FAILED"


def test_kv_like_onboarded_source_unchanged(current):
    for name in (n for n in CORPUS if n.startswith("kv_like_source#")):
        assert current["kv_like"][name] == GOLDEN["pipeline"]["kv_like"][name]
        assert current["kv_like"][name]["adapter_id"] == "golden_kv_like"


# --- TEST 3: syslog-wrapped LEEF/XML stay syslog ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["syslog_wrapped_leef1", "syslog_wrapped_leef2", "syslog_wrapped_xml"])
def test_syslog_wrapped_payloads_remain_syslog(client, current, name):
    raw = CORPUS[name][1]
    assert detect_format(raw) == FormatType.SYSLOG
    for registry in GOLDEN["pipeline"]:
        assert current[registry][name] == GOLDEN["pipeline"][registry][name]
    before = GOLDEN["pipeline"]["shipped"][name]
    e = ingest(client, raw)
    # Exactly the pre-Phase-8 outcome (syslog-wrapped XML was, and stays, a syslog parse FAILURE).
    assert (e["format_detected"], e["adapter_id"], e["status"]) == ("syslog", before["adapter_id"], before["status"])


# --- TESTS 4 + 5: bare LEEF / XML with no onboarded adapter ----------------------------------------------------


@pytest.mark.parametrize("raw", [sc.BARE_LEEF1, sc.BARE_LEEF2])
def test_bare_leef_without_adapter_is_native_leef(client, raw):
    assert detect_format(raw) == FormatType.UNKNOWN  # primary detector unchanged
    assert detect_secondary_format(raw) == FormatType.LEEF
    e = ingest(client, raw)
    assert (e["format_detected"], e["adapter_id"], e["status"]) == ("leef", "leef_generic", "SUCCESS")
    assert e["network"]["src_ip"] == "192.0.2.1" and e["network"]["dst_port"] == 443
    assert e["user"]["name"] == "eve" and e["event_action"] == "Deny" and e["raw_event"] == raw
    assert e["processing_metadata"]["parser"] == "leef"


@pytest.mark.parametrize("raw", [sc.BARE_XML, '<?xml version="1.0" encoding="UTF-8"?>' + sc.BARE_XML])
def test_bare_xml_without_adapter_is_native_xml(client, raw):
    assert detect_format(raw) == FormatType.UNKNOWN
    assert detect_secondary_format(raw) == FormatType.XML
    e = ingest(client, raw)
    assert (e["format_detected"], e["adapter_id"], e["status"]) == ("xml", "xml_generic", "SUCCESS")
    assert e["extensions"]["System.EventID"] == "4624" and e["extensions"]["EventData.Data[0]"] == "bob"
    assert e["extensions"]["EventData.Data[0].@Name"] == "TargetUserName" and e["raw_event"] == raw
    lin = client.get(f"{API}/views/events/{e['event_id']}/lineage").json()
    assert lin["field_accounting"]["unaccounted"] == [] and lin["nothing_silently_discarded"] is True


# --- TEST 6: malformed LEEF/XML: controlled, never a crash -----------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("<Event><System><EventID>1</System></Event>", ("unknown", "FAILED")),
    ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', ("unknown", "FAILED")),
    ("<a>" * 70 + "</a>" * 70, ("unknown", "FAILED")),  # deeper than MAX_DEPTH
    ("LEEF:2.0|Acme|GW|1|E|^|src=1.1.1.1^garbage-token^=nokey", ("leef", "PARTIAL")),
    ("LEEF:2.0|Acme|GW|1|E|zz|src=1.1.1.1\tdst=2.2.2.2", ("leef", "PARTIAL")),
])
def test_malformed_inputs_fail_in_a_controlled_way(client, raw, expected):
    e = ingest(client, raw)
    assert (e["format_detected"], e["status"]) == expected and e["raw_event"] == raw


def test_malformed_leef_attributes_are_preserved_not_dropped(client):
    e = ingest(client, "LEEF:2.0|Acme|GW|1|E|^|src=1.1.1.1^garbage-token^=nokey^src=9.9.9.9")
    assert e["extensions"]["leef_malformed_attributes"] == ["garbage-token", "=nokey"]
    assert e["extensions"]["src__2"] == "9.9.9.9" and e["network"]["src_ip"] == "1.1.1.1"
    assert any("malformed" in w for w in e["warnings"])


# --- TESTS 7 + 8: Phase 3 sandbox / Phase 6 validator gating unchanged -------------------------------------------


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in INFORMATIONAL}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _sample_gating(metrics):
    return [(s["index"], s["matched"], s["claimed_by_existing"], s["mapped_field_count"], s["unmapped_fields"])
            for s in metrics["samples"]]


def test_phase3_sandbox_decisions_and_gating_metrics_identical():
    current = sc.sandbox_scenarios()
    for name, before in GOLDEN["sandbox"].items():
        after = current[name]
        assert after["decision"] == before["decision"] and after["reasons"] == before["reasons"]
        assert _strip(after["metrics"]) == _strip(before["metrics"])
        assert _sample_gating(after["metrics"]) == _sample_gating(before["metrics"])
    # The informational count may only grow, and only for bare LEEF/XML now parsed natively.
    assert any(current[n]["metrics"]["parsed_samples"] > GOLDEN["sandbox"][n]["metrics"]["parsed_samples"] for n in current)


def test_phase6_validator_decisions_and_gating_metrics_identical():
    """Synthetic worst case: the current registry has NO adapter for the bare
    LEEF/XML evidence, so the *descriptions* inside regression entries (which
    normalized fields differ) may change because the "current" output of those
    samples is now native LEEF instead of FAILED. Everything that gates a
    decision must be identical."""
    current = sc.validator_scenarios()
    for name, before in GOLDEN["validator"].items():
        after = current[name]
        for key in ("result", "reasons", "compatibility_confirmation_required", "preservation"):
            assert after[key] == before[key], key
        assert sorted(r["index"] for r in after["regressions"]) == sorted(r["index"] for r in before["regressions"])
        for part in ("new_structure", "historical"):
            if before.get(part):
                assert _strip(after[part]) == _strip(before[part])
                assert _sample_gating(after[part]) == _sample_gating(before[part])


# --- Pre-change oracle: Step 2 acts ONLY through detect_secondary_format -------------------------------------


@pytest.fixture()
def pre_step2(monkeypatch):
    """Force the secondary detector to UNKNOWN: exactly the pre-Step-2 code path."""
    from app.pipeline import orchestrator

    def use():
        monkeypatch.setattr(orchestrator, "detect_secondary_format", lambda raw: FormatType.UNKNOWN)

    def restore():
        monkeypatch.undo()

    return use, restore


def test_oracle_reproduces_every_golden_output(pre_step2):
    use, restore = pre_step2
    use()
    try:
        assert sc.run_pipeline() == GOLDEN["pipeline"]  # all 355 outputs, byte-equivalent
        assert sc.sandbox_scenarios() == GOLDEN["sandbox"] and sc.validator_scenarios() == GOLDEN["validator"]
    finally:
        restore()


def _diff_paths(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for k in sorted(set(a) | set(b)) for p in _diff_paths(a.get(k), b.get(k), f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def _validate_both_ways(pre_step2, foreign: list[str]):
    """Run the realistic Phase 6 validation (runtime registry = shipped + the
    source's own onboarded adapter) with the current code and with the
    pre-Step-2 oracle."""
    from app.adapters.loader import AdapterRegistry
    from app.learning import validation as learning_validation

    adapters = sc.onboarded_adapters()

    def run():
        out = {}
        for name, drifted, historical in (("leef1_kv", sc.LEEF1, sc.LEEF1[:6]),
                                          ("leef2_delimited", sc.LEEF2, sc.LEEF2[:6])):
            adapter = adapters[name]
            registry = AdapterRegistry(sc.registries()["shipped"].all() + [adapter])
            out[name] = learning_validation.validate_candidate(
                adapter, registry, drifted + foreign, historical, issues=[], needs_manual=False, risk_reasons=[],
                changes_mapping=True, min_match_rate=0.9, reject_below_match_rate=0.5, min_mapping_coverage=0.3)
        return json.loads(json.dumps(out, default=str))

    after = run()
    use, restore = pre_step2
    use()
    try:
        before = run()
    finally:
        restore()
    return after, before


def test_realistic_phase6_validation_is_fully_identical(pre_step2):
    """Real Phase 6 validates against the runtime registry, which contains the
    source's own onboarded adapter: nothing changes, descriptions included."""
    after, before = _validate_both_ways(pre_step2, foreign=[])
    assert after == before  # byte-equivalent


def test_unclaimed_bare_xml_evidence_changes_only_informational_fields(pre_step2):
    """If drifted evidence contains a bare XML line that no adapter claims, that
    sample is now parsed natively. Only informational fields may differ; the
    sample stays unmatched and every gating value is identical."""
    after, before = _validate_both_ways(pre_step2, foreign=[sc.BARE_XML])
    allowed = {"parsed_samples", "adapter_id", "error", "field_count", "status", "warnings"}
    paths = _diff_paths(after, before)
    assert paths, "expected the unclaimed bare XML sample to be parsed natively"
    for path in paths:
        assert ".new_structure." in path and path.rsplit(".", 1)[-1] in allowed, path
    for name in after:
        assert after[name]["result"] == before[name]["result"]
        assert [s["matched"] for s in after[name]["new_structure"]["samples"]] ==                [s["matched"] for s in before[name]["new_structure"]["samples"]]


# --- TEST 9: drift reparse and Phase 6 use the same registry --------------------------------------------------------


def test_stored_leef_and_xml_reparse_through_the_registry(client):
    for raw, fmt, key in ((sc.BARE_LEEF2, "leef", "usrName"), (sc.BARE_XML, "xml", "System.EventID")):
        e = ingest(client, raw)
        fields = drift_service._reparse({"format_detected": e["format_detected"], "raw_event": e["raw_event"]})
        assert fields == get_parser(fmt).parse(raw).fields and key in fields
        r = client.post(f"{API}/events/{e['event_id']}/reprocess")
        assert r.status_code == 200 and r.json()["event"]["format_detected"] == fmt


def test_existing_parser_lookup_unchanged():
    from app.pipeline.parsers.cef_parser import CEFParser
    from app.pipeline.parsers.json_parser import JSONParser
    from app.pipeline.parsers.syslog_parser import SyslogParser

    assert isinstance(get_parser("syslog"), SyslogParser) and isinstance(get_parser("json"), JSONParser)
    assert isinstance(get_parser("cef"), CEFParser) and get_parser("delimited") is None and get_parser("kv") is None
    assert [f.value for f in FormatType][:6] == ["syslog", "json", "cef", "delimited", "kv", "unknown"]


def test_unknown_error_message_unchanged():
    assert process("plain garbage").error_message == "Could not detect a known log format (syslog/json/cef)."


def test_api_rejects_non_utf8_raw_logs_at_the_boundary(client):
    """Pre-existing (Phase 0) boundary: a lone surrogate is rejected with 422 and nothing is stored."""
    body = b'{"raw_log":"<r>' + b"\\" + b'ud800</r>"}'  # the JSON escape sequence, sent as raw bytes
    r = client.post(f"{API}/ingest", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
