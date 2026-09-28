"""Phase 8 Step 3: compact lineage codec — deterministic, versioned, strict."""
import itertools
import random

import pytest

from app.phase8 import lineage_codec as codec
from app.phase8.lineage_codec import LineageDecodeError

ALL_OK = {s: codec.OK for s in codec.STAGES}


def test_layout_constants():
    assert len(codec.STAGES) == 9 and codec.STAGE_BITS == 2
    assert codec.STAGES == ("RAW", "FORMAT", "PARSER", "ADAPTER", "NORMALIZATION", "FIELD_ACCOUNTING", "WARNINGS",
                            "DRIFT", "BASELINE")
    assert codec.EXCEPTIONS == ("FAILED", "PARTIAL", "DRIFT", "OVERFLOW", "VAULT_FAILED", "LEARNING", "ROLLBACK",
                                "REPLAY")
    assert codec.TEMPLATE_VERSION == 1 and codec.PACKED_BYTES == 5


def test_known_vector_is_stable():
    # 9 x OK (0b01) = 0x15555, template version 1 at bit 56.
    assert codec.encode_stages(ALL_OK) == 0x0100_0000_0001_5555
    assert codec.encode_exceptions(["FAILED", "REPLAY"]) == 0b1000_0001
    assert codec.pack(0x0100_0000_0001_5555, 0x81) == bytes([1, 0x01, 0x55, 0x55, 0x81])


def test_every_stage_outcome_round_trips():
    for stage, outcome in itertools.product(codec.STAGES, codec.OUTCOMES):
        stages = {**ALL_OK, stage: outcome}
        decoded = codec.decode(codec.encode_stages(stages), 0)
        assert decoded.stages == stages and decoded.template_version == 1


def test_all_exception_masks_round_trip():
    for mask in range(256):
        names = [n for i, n in enumerate(codec.EXCEPTIONS) if mask & (1 << i)]
        assert codec.encode_exceptions(names) == mask
        assert list(codec.decode(codec.encode_stages(ALL_OK), mask).exceptions) == names


def test_random_states_round_trip_through_integers_and_bytes():
    rng = random.Random(8)
    for _ in range(5000):
        stages = {s: rng.choice(codec.OUTCOMES) for s in codec.STAGES}
        exceptions = [n for n in codec.EXCEPTIONS if rng.random() < 0.3]
        lin = codec.CompactLineage(1, stages, tuple(exceptions))
        stage_mask, exc_mask = codec.encode(lin)
        assert codec.encode(lin) == (stage_mask, exc_mask)  # deterministic
        assert codec.decode(stage_mask, exc_mask, template_version=1) == lin
        assert codec.unpack(codec.pack(stage_mask, exc_mask)) == lin
        assert 0 <= stage_mask < 2 ** 63 and 0 <= exc_mask < 2 ** 31  # fits BIGINT / INTEGER


def test_is_exception_and_worst():
    assert not codec.CompactLineage(1, ALL_OK, ()).is_exception
    assert codec.CompactLineage(1, {**ALL_OK, "DRIFT": codec.SKIPPED}, ()).worst == codec.OK
    assert codec.CompactLineage(1, {**ALL_OK, "WARNINGS": codec.WARN}, ()).is_exception
    assert codec.CompactLineage(1, ALL_OK, ("OVERFLOW",)).is_exception
    assert codec.CompactLineage(1, {**ALL_OK, "PARSER": codec.FAIL}, ()).worst == codec.FAIL


@pytest.mark.parametrize("stage_mask,exc_mask,column,fragment", [
    (-1, 0, None, "non-negative"),
    (0x0100_0000_0001_5555, -1, None, "non-negative"),
    (0x0200_0000_0001_5555, 0, None, "Unsupported lineage template version v2"),
    (0x0001_5555, 0, None, "Unsupported lineage template version v0"),
    (0x0100_0000_0001_5555, 0, 2, "Template version mismatch"),
    (0x0100_0000_0001_5555 | (1 << 20), 0, None, "reserved bits"),
    (0x0100_0000_0001_5555, 1 << 8, None, "reserved bits"),
    (1 << 63, 0, None, "63 bits"),
])
def test_malformed_or_mismatched_input_is_rejected_explicitly(stage_mask, exc_mask, column, fragment):
    with pytest.raises(LineageDecodeError, match=fragment):
        codec.decode(stage_mask, exc_mask, template_version=column)


def test_unpack_rejects_wrong_length_and_reserved_bits():
    with pytest.raises(LineageDecodeError):
        codec.unpack(b"\x01\x00\x00\x00")
    with pytest.raises(LineageDecodeError):
        codec.unpack(bytes([1, 0xFF, 0xFF, 0xFF, 0]))  # bits 18..23 of the stage field


def test_encode_rejects_incomplete_or_unknown_input():
    with pytest.raises(ValueError, match="Missing"):
        codec.encode_stages({"RAW": codec.OK})
    with pytest.raises(ValueError, match="Unknown lineage stage"):
        codec.encode_stages({**ALL_OK, "LEARNING_HISTORY": codec.OK})
    with pytest.raises(ValueError, match="Unknown outcome"):
        codec.encode_stages({**ALL_OK, "RAW": "MAYBE"})
    with pytest.raises(ValueError, match="Unknown lineage exception"):
        codec.encode_exceptions(["NOPE"])
    with pytest.raises(ValueError, match="template version"):
        codec.encode_stages(ALL_OK, version=2)


def test_projection_from_detailed_chain():
    chain = [{"stage": s, "outcome": codec.OK} for s in
             ("RAW", "FORMAT_DETECTION", "PARSER", "ADAPTER", "NORMALIZATION", "FIELD_ACCOUNTING", "WARNINGS",
              "DRIFT_DECISION", "BASELINE", "LEARNING_HISTORY")]
    chain[7]["outcome"] = codec.WARN
    assert codec.from_detailed_chain(chain) == {**ALL_OK, "DRIFT": codec.WARN}
    with pytest.raises(ValueError, match="lacks"):
        codec.from_detailed_chain(chain[:3])
    with pytest.raises(ValueError, match="twice"):
        codec.from_detailed_chain(chain + [{"stage": "RAW", "outcome": codec.OK}])
