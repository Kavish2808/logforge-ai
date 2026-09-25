"""Deterministic multi-sample evidence."""
import json

from app.onboarding.analysis import analyze_samples, classify_value


def kv_samples(n=12, optional_every=3):
    out = []
    for i in range(n):
        extra = f" rule=web-{i}" if optional_every and i % optional_every == 0 else ""
        out.append(f"vendor=ACMEFW ts=2026-01-18T12:{i:02d}:00Z srcip=10.0.0.{i + 1} dstip=8.8.{i}.8 "
                   f"srcport={40000 + i} dstport=443 action=allow sev=high{extra}")
    return out


def test_kv_evidence():
    a = analyze_samples(kv_samples())
    assert a["sample_count"] == 12
    assert a["dominant_format"] == "kv"
    assert a["parsed_in_dominant_format"] == 12
    assert a["parser_hint"]["strategy"] == "kv"
    f = a["fields"]
    assert f["srcip"]["dominant_class"] == "ipv4" and f["srcip"]["present_in"] == 12
    assert f["ts"]["dominant_class"] == "timestamp"
    assert f["srcport"]["dominant_class"] == "integer" and f["srcport"]["integer_range"] == [40000, 40011]
    assert f["sev"]["dominant_class"] == "severity_word"
    assert f["vendor"]["constant_value"] == "ACMEFW"
    assert a["optional_fields"] == ["rule"] and f["rule"]["present_in"] == 4
    assert "srcip" in a["common_fields"]
    assert a["vendor_indicators"][0]["field"] == "vendor"
    assert a["structure_variants"] == 2


def test_no_invented_fields():
    samples = kv_samples()
    a = analyze_samples(samples)
    observed = set()
    for s in samples:
        observed |= {token.split("=")[0] for token in s.split()}
    assert set(a["fields"]) == observed


def test_delimited_inference():
    samples = [f"2026-01-18T12:00:{i:02d}Z|ACMEFW|10.0.0.{i}|8.8.8.8|{40000 + i}|443|allow" for i in range(10)]
    a = analyze_samples(samples)
    assert a["dominant_format"] == "delimited"
    assert a["parser_hint"]["delimiter"] == "|" and a["parser_hint"]["column_count"] == 7
    assert a["fields"]["col_3"]["dominant_class"] == "ipv4"
    assert a["fields"]["col_2"]["constant_value"] == "ACMEFW"


def test_native_json_and_mixed_formats():
    samples = [json.dumps({"product": "Widget", "src_ip": f"10.0.0.{i}", "n": i}) for i in range(8)]
    samples += ["unparseable junk", "more junk"]
    a = analyze_samples(samples)
    assert a["dominant_format"] == "json"
    assert a["parsed_in_dominant_format"] == 8
    assert a["format_counts"]["json"] == 8
    assert len(a["parse_failures"]) == 2


def test_fewer_samples_are_handled():
    a = analyze_samples(kv_samples(n=2, optional_every=0))
    assert a["sample_count"] == 2 and a["dominant_format"] == "kv"


def test_value_classes():
    assert classify_value("10.0.0.1") == "ipv4"
    assert classify_value("::1") == "ipv6"
    assert classify_value("443") == "integer"
    assert classify_value(443) == "integer"
    assert classify_value("1705650300") == "epoch"
    assert classify_value("Jan 18 12:00:00") == "timestamp"
    assert classify_value("Warning") == "severity_word"
    assert classify_value("hello") == "string"
    assert classify_value(None) == "null"


def test_analysis_is_deterministic():
    assert analyze_samples(kv_samples()) == analyze_samples(kv_samples())
