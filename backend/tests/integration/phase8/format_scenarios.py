"""Deterministic format-priority scenarios for Phase 8 Step 2.

`python -m tests.integration.phase8.format_scenarios` writes the golden file
from the CURRENT code. The golden in this repository was captured BEFORE the
LEEF/XML priority hooks existed (pre-Phase-8 behavior); the tests re-run the
same scenarios against the current code and compare.

Scenario registries: the shipped YAML adapters alone, and the shipped adapters
plus one Phase 3 onboarded adapter built exactly as Phase 3 builds it
(deterministic analysis -> proposal -> evidence review -> to_adapter).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adapters.loader import AdapterRegistry, get_adapter_registry
from app.demo import fixtures as demo_fixtures
from app.learning import validation as learning_validation
from app.onboarding import sandbox
from app.onboarding.analysis import analyze_samples
from app.onboarding.providers import build_offline_proposal, parse_proposal
from app.onboarding.proposal import review_proposal, to_adapter
from app.pipeline.orchestrator import process

GOLDEN = Path(__file__).parent / "golden" / "format_priority_golden.json"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RECEIVED_AT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

LEEF1 = [f"LEEF:1.0|IBM|QRadar|7.4|Login|src=10.1.1.{i}\tdst=10.2.2.2\tusrName=bob{i}\tsev=3\tproto=TCP" for i in range(12)]
LEEF2 = [f"LEEF:2.0|Lancope|StealthWatch|1.0|41|^|src=10.0.1.{i}^dst=10.0.0.5^sev=5^cat=anomaly^srcPort={80 + i}"
         f"^dstPort=21^usrName=joe{i}" for i in range(12)]
KV_LIKE = [f"LEEF=1.0 vendor=Acme product=FW src=10.0.0.{i} dst=10.0.0.9 act=allow sev={i % 5}" for i in range(12)]
BARE_LEEF1 = "LEEF:1.0|Acme|Gateway|2.1|Deny|src=192.0.2.1\tdst=198.51.100.7\tsrcPort=5555\tdstPort=443\tproto=TCP\tusrName=eve"
BARE_LEEF2 = ("LEEF:2.0|Acme|Gateway|2.1|Deny|^|src=192.0.2.1^dst=198.51.100.7^srcPort=5555^dstPort=443^proto=TCP"
              "^usrName=eve^sev=7")
BARE_XML = ('<Event><System><Provider Name="Acme"/><EventID>4624</EventID><Computer>WS01</Computer></System>'
            '<EventData><Data Name="TargetUserName">bob</Data><Data Name="IpAddress">10.9.8.7</Data></EventData></Event>')

# kind: "unchanged" = must be byte-identical before/after Step 2;
#       "native" = a valid bare LEEF/XML event that MAY change only where no onboarded adapter claimed it.
EDGE_CASES: list[tuple[str, str, str]] = [
    ("syslog_wrapped_leef1", "unchanged", "<13>Jan 18 11:07:53 host " + BARE_LEEF1),
    ("syslog_wrapped_leef2", "unchanged", "<13>Jan 18 11:07:53 host " + BARE_LEEF2),
    ("syslog_wrapped_xml", "unchanged", "<13>Jan 18 11:07:53 host " + BARE_XML),
    ("cef_with_leef_value", "unchanged", "CEF:0|Acme|FW|1|100|Blocked|5|src=1.1.1.1 msg=LEEF:1.0|x|y|z|w|"),
    ("json_with_leef_value", "unchanged", json.dumps({"user": "a", "note": "LEEF:1.0|x|y|z|w|", "action": "login"})),
    ("json_xml_lookalike", "unchanged", json.dumps({"payload": BARE_XML, "action": "x"})),
    ("kv_like_leef", "unchanged", KV_LIKE[0]),
    ("leef_signature_mid_line", "unchanged", "vendor=Acme msg=LEEF:1.0|IBM|QRadar|7.4|Login| src=1.1.1.1"),
    ("leef_unknown_version", "unchanged", "LEEF:3.0|Acme|Gateway|2.1|Deny|src=1.1.1.1"),
    ("leef_truncated_header", "unchanged", "LEEF:1.0|Acme|Gateway"),
    ("xml_with_doctype", "unchanged", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'),
    ("xml_malformed", "unchanged", "<Event><System><EventID>1</System></Event>"),
    ("html_like_text", "unchanged", "<b>not a log</b> but text afterwards"),
    ("plain_garbage", "unchanged", "totally unknown \t format with\ttabs"),
    ("bare_leef1", "native", BARE_LEEF1),
    ("bare_leef2", "native", BARE_LEEF2),
    ("bare_xml", "native", BARE_XML),
    ("bare_xml_declared", "native", '<?xml version="1.0" encoding="UTF-8"?>' + BARE_XML),
]


def corpus() -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(FIXTURES).as_posix()
        if path.suffix == ".json":
            items.append((f"fixture:{rel}", "unchanged", text.strip("\n")))
        else:
            for n, line in enumerate(line for line in text.splitlines() if line.strip()):
                items.append((f"fixture:{rel}#{n}", "unchanged", line))
    for f in demo_fixtures.FIXTURES:
        items.append((f"demo:{f.key}", "unchanged", f.raw))
    items.extend(EDGE_CASES)
    for i, raw in enumerate(LEEF1[:3]):
        items.append((f"leef1_source#{i}", "native", raw))
    for i, raw in enumerate(LEEF2[:3]):
        items.append((f"leef2_source#{i}", "native", raw))
    for i, raw in enumerate(KV_LIKE[:3]):
        items.append((f"kv_like_source#{i}", "unchanged", raw))
    return items


def _offline_adapter(adapter_id: str, samples: list[str]):
    analysis = analyze_samples(samples)
    proposal = parse_proposal(build_offline_proposal(analysis))
    review = review_proposal(proposal, analysis)
    assert not review["issues"], review["issues"]
    return to_adapter(proposal, review["accepted"], adapter_id=adapter_id, version=1, description="golden scenario")


LEEF2_HUMAN_PROPOSAL = {
    "vendor": "Lancope", "product": "StealthWatch", "format": "delimited",
    "parser": {"strategy": "delimited", "delimiter": "|",
               "columns": ["col_1", "col_2", "col_3", "col_4", "col_5", "col_6", "col_7"]},
    "match": {"field": "col_2", "equals": "Lancope"},
    "ocsf_class_uid": 4001,
    "mappings": [{"raw_field": "col_5", "target": "event_action", "confidence": 0.9, "evidence": "event id column"},
                 {"raw_field": "col_4", "target": "product_version", "confidence": 0.9, "evidence": "version column"}],
    "overall_confidence": 0.9,
}


def _human_adapter(adapter_id: str, samples: list[str], proposal: dict[str, Any]):
    analysis = analyze_samples(samples)
    parsed = parse_proposal(proposal)
    review = review_proposal(parsed, analysis)
    assert not review["issues"], review["issues"]
    return to_adapter(parsed, review["accepted"], adapter_id=adapter_id, version=1, description="golden scenario")


def onboarded_adapters() -> dict[str, Any]:
    demo_samples = [f.raw for f in demo_fixtures.of_kind("SAMPLE_V1")]
    return {
        "leef1_kv": _offline_adapter("golden_leef1_kv", LEEF1),
        "kv_like": _offline_adapter("golden_kv_like", KV_LIKE),
        "leef2_delimited": _human_adapter("golden_leef2_delimited", LEEF2, LEEF2_HUMAN_PROPOSAL),
        "demo_kv": _offline_adapter("golden_demo_kv", demo_samples),
    }


def registries() -> dict[str, AdapterRegistry]:
    shipped = get_adapter_registry()
    out = {"shipped": AdapterRegistry(shipped.all())}
    for name, adapter in onboarded_adapters().items():
        out[name] = AdapterRegistry(shipped.all() + [adapter])
    return out


def serialize(result) -> dict[str, Any]:
    data = asdict(result)
    data.pop("processed_at")  # wall clock; everything else is deterministic
    return json.loads(json.dumps(data, sort_keys=True, default=str))


def run_pipeline() -> dict[str, dict[str, Any]]:
    regs = registries()
    return {reg: {name: serialize(process(raw, received_at=RECEIVED_AT, adapter_registry=registry))
                  for name, _, raw in corpus()}
            for reg, registry in regs.items()}


# Informational-only sandbox fields: may legitimately differ once unmatched
# bare LEEF samples are parsed natively instead of FAILED.
INFORMATIONAL = ("parsed_samples", "samples")


def sandbox_scenarios() -> dict[str, dict[str, Any]]:
    adapters = onboarded_adapters()
    base = registries()["shipped"]
    mixed = LEEF1 + LEEF2[:3] + [BARE_XML, "garbage line"]
    scenarios = {
        "leef1_candidate_on_mixed": (adapters["leef1_kv"], mixed),
        "leef2_candidate_on_mixed": (adapters["leef2_delimited"], LEEF2 + LEEF1[:3] + [BARE_XML]),
        "kv_like_candidate_on_mixed": (adapters["kv_like"], KV_LIKE + LEEF1[:2] + LEEF2[:2]),
    }
    out = {}
    for name, (candidate, samples) in scenarios.items():
        metrics = sandbox.run_sandbox(samples, candidate, base)
        review = {"issues": [], "accepted": [], "rejected": []}
        result, reasons = sandbox.decide(review, metrics, min_match_rate=0.9, reject_below_match_rate=0.5,
                                         min_mapping_coverage=0.3)
        out[name] = json.loads(json.dumps({"metrics": metrics, "decision": result, "reasons": reasons}, default=str))
    return out


def validator_scenarios() -> dict[str, dict[str, Any]]:
    adapters = onboarded_adapters()
    base = registries()["shipped"]
    cases = {
        "leef1_candidate": (adapters["leef1_kv"], LEEF1 + LEEF2[:2] + [BARE_XML], LEEF1[:5]),
        "leef2_candidate": (adapters["leef2_delimited"], LEEF2 + [BARE_LEEF1], LEEF2[:4] + LEEF1[:2]),
    }
    out = {}
    for name, (candidate, drifted, historical) in cases.items():
        result = learning_validation.validate_candidate(
            candidate, base, drifted, historical, issues=[], needs_manual=False, risk_reasons=[],
            changes_mapping=True, min_match_rate=0.9, reject_below_match_rate=0.5, min_mapping_coverage=0.3)
        out[name] = json.loads(json.dumps(result, default=str))
    return out


def capture() -> dict[str, Any]:
    return {"note": "Captured from pre-Phase-8-Step-2 code (HEAD d61f540 + approved Step 1 hooks).",
            "received_at": RECEIVED_AT.isoformat(),
            "corpus": [[n, k, r] for n, k, r in corpus()],
            "pipeline": run_pipeline(), "sandbox": sandbox_scenarios(), "validator": validator_scenarios()}


if __name__ == "__main__":
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(capture(), indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
