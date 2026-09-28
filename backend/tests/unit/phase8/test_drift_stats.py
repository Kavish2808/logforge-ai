"""Phase 8 Step 4: statistical signals and semantic advisories (pure)."""
import math
from collections import Counter

import pytest

from app.phase8 import drift_semantic as sem
from app.phase8 import drift_stats as ds

REQUIRED = {"field", "metric", "baseline_value", "current_value", "deviation", "threshold", "severity", "reason",
            "is_finding", "evidence", "explanation"}


def by_metric(signals):
    return {s.metric: s for s in signals}


def test_psi_known_value_and_identity():
    base, cur = Counter({"a": 50, "b": 50}), Counter({"a": 90, "b": 10})
    expected = 0.4 * math.log(0.9 / 0.5) + (-0.4) * math.log(0.1 / 0.5)
    assert ds.psi(base, cur)[0] == round(expected, 6)
    assert ds.psi(base, base)[0] == 0.0


def test_top_k_bucketing_is_deterministic_with_ties_by_value():
    base = Counter({f"v{i:02d}": 1 for i in range(30)})
    cats = ds.top_categories(base, base, k=5)
    assert cats == ["v00", "v01", "v02", "v03", "v04"]
    bucket = ds.bucketed(base, cats)
    assert bucket[ds.OTHER] == 25 and sum(bucket.values()) == 30


def test_each_signal_is_explained_and_complete():
    base = ["allow"] * 150 + ["deny"] * 50 + [None] * 10
    cur = ["allow"] * 40 + ["blocked"] * 120 + [None] * 50
    signals = ds.compare_field("event_action", base, cur, numeric=False)
    for s in signals:
        d = s.as_dict()
        assert REQUIRED <= set(d) and d["explanation"] and "baseline_events" in d["evidence"]
        assert (d["severity"] is None) == (d["deviation"] < d["threshold"])
    m = by_metric(signals)
    assert m["NEW_CATEGORY_SHARE"].deviation == round(120 / 160, 6) and m["NEW_CATEGORY_SHARE"].severity == "HIGH"
    assert m["VANISHED_CATEGORY_SHARE"].deviation == 0.25 and m["VANISHED_CATEGORY_SHARE"].severity == "MEDIUM"
    assert m["NULL_RATE_CHANGE"].deviation == round(50 / 210 - 10 / 210, 6)
    assert m["PSI"].is_finding and m["PSI"].reason == "PSI_AT_OR_ABOVE_THRESHOLD"


def test_identical_windows_produce_no_findings():
    vals = [str(i % 7) for i in range(300)]
    assert not any(s.is_finding for s in ds.compare_field("severity", vals, list(vals), numeric=True))


def test_cardinality_uses_equal_size_samples():
    base = [f"u{i % 10}" for i in range(2000)]  # 10 distinct, large window
    cur = [f"u{i}" for i in range(200)]  # 200 distinct, small window
    s = by_metric(ds.compare_field("user.domain", base, cur, numeric=False))["CARDINALITY_RATIO"]
    assert s.evidence["equal_sample_size"] == 200 and s.baseline_value == {"distinct": 10}
    assert s.deviation == 20.0 and s.severity == "HIGH"
    same = by_metric(ds.compare_field("user.domain", base, base[:200], numeric=False))["CARDINALITY_RATIO"]
    assert same.deviation == 1.0 and not same.is_finding


def test_median_shift_relative_to_iqr_and_zero_iqr():
    base = [str(p) for p in [443] * 100 + [80] * 100]
    cur = [str(p) for p in [8443] * 200]
    s = by_metric(ds.compare_field("network.dst_port", base, cur, numeric=True))["MEDIAN_SHIFT_IQR"]
    assert s.baseline_value["iqr"] > 0 and s.is_finding and s.severity == "HIGH"
    flat = by_metric(ds.compare_field("network.dst_port", ["443"] * 200, ["444"] * 200, numeric=True))["MEDIAN_SHIFT_IQR"]
    assert flat.evidence["iqr_zero_scaled_by_one"] is True and flat.deviation == 1.0 and flat.severity == "MEDIUM"
    assert "MEDIAN_SHIFT_IQR" not in by_metric(ds.compare_field("severity", ["1"] * 200, ["2"] * 200, numeric=False))


def test_one_sided_empty_field_only_reports_null_rate():
    signals = ds.compare_field("network.direction", [None] * 200, ["inbound"] * 200, numeric=False)
    assert [s.metric for s in signals] == ["NULL_RATE_CHANGE"] and signals[0].deviation == 1.0


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("  ", None), (443, "443"), (443.0, "443"),
                                            (True, "true"), (" x ", "x")])
def test_normalize_value(value, expected):
    assert ds.normalize_value(value) == expected


def test_extension_fields_are_numeric_only_when_all_values_are():
    assert ds.is_numeric_field("extensions.bytes", [str(i) for i in range(30)])
    assert not ds.is_numeric_field("extensions.bytes", [str(i) for i in range(30)] + ["n/a"])
    assert ds.is_numeric_field("network.dst_port", [])


def test_profile_is_bounded_and_psi_from_profiles():
    vals = [f"v{i % 50}" for i in range(1000)]
    p = ds.profile(vals, numeric=False, k=20)
    assert len(p["top"]) == 21 and p["top"][ds.OTHER] == 600 and p["distinct"] == 50
    assert ds.psi_from_profiles(p, p) == 0.0


# --- semantic advisories -------------------------------------------------------------------------------------


def rows(action_ctx):
    out = []
    for action, sev, proto, port, direction, n in action_ctx:
        out += [{"event_action": action, "severity": sev, "network.protocol": proto, "network.dst_port": port,
                 "network.direction": direction}] * n
    return out


BASE = rows([("deny", "high", "tcp", "443", "inbound", 100), ("allow", "low", "tcp", "80", "outbound", 100)])
RELABELED = rows([("blocked", "high", "tcp", "443", "inbound", 100), ("allow", "low", "tcp", "80", "outbound", 100)])
STABLE = (True, {"signature_coverage": 1.0, "phase5_drift_events_current": 0, "baseline_signatures": 1,
                 "min_signature_coverage": 0.95})


def test_relabel_pairs_vanished_and_new_values_with_the_same_context():
    adv = sem.action_relabel(BASE, RELABELED, stable_fields=set(sem.CONTEXT_FIELDS), structure=STABLE)
    assert adv is not None and adv.metric == sem.RELABEL
    assert adv.evidence["pairs"] == [{"baseline_value": "deny", "current_value": "blocked", "context_similarity": 1.0,
                                      "share_ratio": 1.0, "baseline_share": 0.5, "current_share": 0.5}]
    assert "'deny' -> 'blocked'" in adv.explanation and "Advisory only" in adv.explanation


def test_relabel_requires_stable_structure_and_context():
    assert sem.action_relabel(BASE, RELABELED, stable_fields=set(sem.CONTEXT_FIELDS) - {"severity"},
                              structure=STABLE) is None
    assert sem.action_relabel(BASE, RELABELED, stable_fields=set(sem.CONTEXT_FIELDS),
                              structure=(False, STABLE[1])) is None
    other_ctx = rows([("blocked", "low", "udp", "53", "outbound", 100), ("allow", "low", "tcp", "80", "outbound", 100)])
    assert sem.action_relabel(BASE, other_ctx, stable_fields=set(sem.CONTEXT_FIELDS), structure=STABLE) is None


def test_severity_remap():
    remapped = rows([("deny", "critical", "tcp", "443", "inbound", 100), ("allow", "low", "tcp", "80", "outbound", 100)])
    adv = sem.severity_remap(BASE, remapped, action_stable=True, structure=STABLE)
    assert adv is not None and adv.deviation == 0.5
    assert adv.evidence["changes"][0] == {"event_action": "deny", "baseline_modal_severity": "high",
                                          "current_modal_severity": "critical", "baseline_events": 100,
                                          "current_events": 100}
    assert sem.severity_remap(BASE, remapped, action_stable=False, structure=STABLE) is None
    assert sem.severity_remap(BASE, BASE, action_stable=True, structure=STABLE) is None


def test_structure_stability():
    assert sem.structure_stable(["a"] * 10, ["a"] * 100, 0)[0]
    assert not sem.structure_stable(["a"] * 10, ["a"] * 94 + ["b"] * 6, 0)[0]
    assert not sem.structure_stable(["a"] * 10, ["a"] * 100, 1)[0]
