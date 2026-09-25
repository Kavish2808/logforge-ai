"""Demo Mode fixtures are deterministic, namespaced, and shaped for the demo story."""
import hashlib
import re

from app.demo import fixtures as fx
from app.onboarding.proposal import ADAPTER_ID_RE
from app.pipeline.parsers.declarative import split_kv_pairs


def test_fixtures_are_byte_identical_on_every_build():
    assert fx._build() == list(fx.FIXTURES)
    # Golden hashes: any change to the demo logs must be deliberate.
    assert fx.BY_KEY["unknown_probe"].sha256 == "9beadcfe158b515eaf4fcec8507adcad5bb18fcf0bf3c4fec377a46e4205feb7"
    assert fx.BY_KEY["drift_trigger"].sha256 == "00c2b03d21e44fc249dfb1a4b1d0531c2cf684569ab94f4ef5dd8ecbff2bfc13"


def test_hash_is_sha256_of_the_exact_raw_log():
    for f in fx.FIXTURES:
        assert f.sha256 == hashlib.sha256(f.raw.encode("utf-8")).hexdigest()
        assert fx.fixture_for_hash(f.sha256) is f


def test_every_fixture_is_unique_and_carries_the_demo_marker():
    assert len({f.key for f in fx.FIXTURES}) == len(fx.FIXTURES)
    assert len(fx.raw_hashes()) == len(fx.FIXTURES)  # no two fixtures share a raw log
    for f in fx.FIXTURES:
        assert f"device={fx.DEMO_MARKER}" in f.raw
        assert "vendor=LogForgeDemo" in f.raw


def test_namespace_is_a_valid_adapter_id_and_cannot_collide_with_shipped_adapters():
    assert ADAPTER_ID_RE.match(fx.DEMO_SOURCE_KEY)
    from app.adapters.loader import get_adapter_registry
    assert fx.DEMO_SOURCE_KEY not in {a.id for a in get_adapter_registry().all()}


def test_story_shapes():
    assert len(fx.of_kind("SAMPLE_V1")) == 12  # + the failed probe event = 13 onboarding samples (10-15)
    assert len(fx.of_kind("EVENT_V1")) >= 5 and len(fx.of_kind("EVIDENCE_V2")) >= 5

    def keys(raw):
        return list(split_kv_pairs(raw, "whitespace", "="))

    v1, v2 = keys(fx.BY_KEY["event_v1_01"].raw), keys(fx.BY_KEY["drift_trigger"].raw)
    assert v1 == ["timestamp", "vendor", "product", "device", "src_ip", "dst_ip", "src_port", "dst_port",
                  "protocol", "action", "severity", "message"]
    # v2: dst_port renamed in place to dst_port_number, policy_id and zone appended.
    assert v2 == [k if k != "dst_port" else "dst_port_number" for k in v1] + ["policy_id", "zone"]
    for f in fx.of_kind("CHECK_V1") + fx.of_kind("POST_ROLLBACK_V1") + fx.of_kind("EVENT_V1"):
        assert keys(f.raw) == v1
    for f in fx.of_kind("EVIDENCE_V2") + fx.of_kind("CHECK_V2"):
        assert keys(f.raw) == v2


def test_malformed_record_is_actually_malformed():
    raw = fx.BY_KEY["malformed"].raw
    assert raw.count('"') % 2 == 1  # unterminated quote
    assert re.search(r'message="[^"]*$', raw)
