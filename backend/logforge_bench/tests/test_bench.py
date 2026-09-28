"""Unit tests for the benchmark harness itself (no database, no network).

Run: docker compose exec backend pytest -q logforge_bench/tests
"""
from __future__ import annotations

from pathlib import Path

import pytest

from logforge_bench import dataset, metrics, report, runner


def test_dataset_is_deterministic_and_unique():
    a = dataset.generate(500, seed=1337)
    b = dataset.generate(500, seed=1337)
    assert [e.raw for e in a] == [e.raw for e in b]
    well_formed = [e.raw for e in a if not e.malformed]
    assert len(set(well_formed)) == len(well_formed)  # well-formed raw logs are unique
    # Some malformed kinds are constant strings (e.g. a truncated CEF header), so identical raw logs
    # can be sent; the integrity check compares the stored SHA-256 multiset with the one sent.
    assert {e.raw for e in a if e.malformed_kind == "truncated_cef_header"} <= {"CEF:0|Palo Alto Networks|PAN-OS"}
    assert [e.raw for e in dataset.generate(50, seed=7)] != [e.raw for e in a[:50]]


def test_dataset_covers_every_format_and_band_round_robin():
    events = dataset.generate(240, seed=1337)
    d = dataset.describe(events)
    assert set(d["by_format"]) == set(dataset.FORMATS)
    assert set(d["sizes_by_band"]) == set(dataset.SIZE_BANDS)
    assert len(set(d["by_format"].values())) == 1  # 240 = 10 x 24 combos -> exactly balanced


@pytest.mark.parametrize("band,target", sorted(dataset.SIZE_BANDS.items()))
def test_well_formed_sizes_are_near_their_band(band, target):
    events = [e for e in dataset.generate(600, seed=1337, sizes=(band,), malformed_rate=0.0)]
    sizes = sorted(e.size_bytes for e in events)
    median = sizes[len(sizes) // 2]
    assert 0.7 * target <= median <= 1.3 * target


def test_malformed_rate_is_close_to_target_and_labelled():
    events = dataset.generate(10_000, seed=1337)
    rate = sum(e.malformed for e in events) / len(events)
    assert 0.035 <= rate <= 0.065
    assert all(e.malformed_kind for e in events if e.malformed)
    assert not any(e.malformed_kind for e in events if not e.malformed)


def test_warmup_indices_never_collide_with_measured_events():
    # Well-formed events never collide; constant malformed strings (e.g. a truncated CEF header) can, which
    # is why the integrity check includes warm-up events in the multiset of raw logs sent.
    measured = {e.raw for e in dataset.generate(1000, seed=1337) if not e.malformed}
    warm = {e.raw for e in dataset.generate(100, start=runner.WARMUP_INDEX_OFFSET, seed=1337) if not e.malformed}
    assert not measured & warm


def test_percentiles_use_all_samples():
    s = metrics.latency_summary([float(x) for x in range(1, 101)])
    assert s["n"] == 100 and s["min"] == 1.0 and s["max"] == 100.0
    assert s["p50"] == pytest.approx(50.5) and s["p99"] == pytest.approx(99.01)


def _windows(eps: list[float], p95: list[float], errors: list[int] | None = None):
    errors = errors or [0] * len(eps)
    return [{"t_start_s": i * 10.0, "events_per_sec": e, "p95_ms": p, "harness_errors": x}
            for i, (e, p, x) in enumerate(zip(eps, p95, errors))]


def test_degradation_flags_throughput_collapse_and_latency_growth():
    d = runner.degradation(_windows([50] * 3 + [45] * 3 + [30] * 3, [100] * 6 + [200] * 3))
    assert "THROUGHPUT_COLLAPSE" in d["flags"] and "LATENCY_GROWTH" in d["flags"]


def test_degradation_is_quiet_for_a_flat_run_and_refuses_short_runs():
    assert runner.degradation(_windows([40] * 10, [100] * 10))["flags"] == []
    assert runner.degradation(_windows([40] * 4, [100] * 4))["verdict"] == "INSUFFICIENT DATA"


def test_error_accumulation_needs_more_errors_later():
    assert "ERROR_ACCUMULATION" in runner.degradation(_windows([40] * 8, [100] * 8, [0, 0, 0, 0, 1, 2, 3, 4]))["flags"]
    assert "ERROR_ACCUMULATION" not in runner.degradation(_windows([40] * 8, [100] * 8, [3, 0, 0, 0, 0, 0, 0, 0]))["flags"]


def test_docker_window_selects_only_samples_inside_the_run(tmp_path: Path):
    p = tmp_path / "ds.jsonl"
    p.write_text("\n".join([
        '{"ts":"2026-01-01T00:00:00Z","Name":"logforge-db","CPUPerc":"10.00%","MemUsage":"100MiB / 7GiB"}',
        '{"ts":"2026-01-01T00:00:10Z","Name":"logforge-db","CPUPerc":"30.00%","MemUsage":"120MiB / 7GiB"}',
        '{"ts":"2026-01-01T00:01:00Z","Name":"logforge-db","CPUPerc":"90.00%","MemUsage":"300MiB / 7GiB"}',
        "not json",
    ]))
    rows = report._load_docker([p])
    w = report._docker_window(rows, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:20+00:00")
    assert w["logforge-db"] == {"samples": 2, "cpu_mean": 20.0, "cpu_max": 30.0, "mem_max_mib": 120.0,
                                "mem_first_mib": 100.0, "mem_last_mib": 120.0}


def test_application_code_never_imports_the_benchmark():
    app_dir = Path(__file__).resolve().parents[2] / "app"
    offenders = [str(f) for f in app_dir.rglob("*.py") if "logforge_bench" in f.read_text(encoding="utf-8")]
    assert offenders == []
