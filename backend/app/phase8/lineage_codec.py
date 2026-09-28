"""Compact, exception-based lineage codec (Phase 8, template version 1).

Pure and DB-free. A lineage row is two integers:

stage_mask (BIGINT, 64-bit signed, always non-negative)
    bits  0..17  nine stages x 2 bits, stage i at bits (2i, 2i+1)
    bits 18..55  reserved, must be zero
    bits 56..62  template version (7 bits), so the integer is self-describing
    bit  63      sign bit, must be zero

exception_mask (INTEGER)
    bits 0..7    FAILED, PARTIAL, DRIFT, OVERFLOW, VAULT_FAILED, LEARNING,
                 ROLLBACK, REPLAY
    bits 8..31   reserved, must be zero

Each stage holds one of the four outcomes the detailed lineage API reports
(views_service.lineage): SKIPPED=0, OK=1, WARN=2, FAIL=3 — ordered by
severity, so "worst stage" is a plain max. The compact form is a projection of
the detailed chain (stage -> outcome) plus exception flags; the detailed
lineage stays the source of truth for summaries and details and is never
replaced.

Decoding is strict: a negative value, a reserved bit, an unknown template
version or a version that disagrees with the stored template_version column
raises LineageDecodeError. Nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

TEMPLATE_VERSION = 1

STAGES: tuple[str, ...] = (
    "RAW", "FORMAT", "PARSER", "ADAPTER", "NORMALIZATION", "FIELD_ACCOUNTING", "WARNINGS", "DRIFT", "BASELINE",
)
# Detailed-lineage stage name -> compact stage name (LEARNING_HISTORY is not a
# compact stage; learning involvement is the LEARNING exception bit).
DETAILED_TO_COMPACT: dict[str, str] = {
    "RAW": "RAW", "FORMAT_DETECTION": "FORMAT", "PARSER": "PARSER", "ADAPTER": "ADAPTER",
    "NORMALIZATION": "NORMALIZATION", "FIELD_ACCOUNTING": "FIELD_ACCOUNTING", "WARNINGS": "WARNINGS",
    "DRIFT_DECISION": "DRIFT", "BASELINE": "BASELINE",
}

SKIPPED, OK, WARN, FAIL = "SKIPPED", "OK", "WARN", "FAIL"
OUTCOMES: tuple[str, ...] = (SKIPPED, OK, WARN, FAIL)
_OUTCOME_CODE = {name: code for code, name in enumerate(OUTCOMES)}

EXCEPTIONS: tuple[str, ...] = (
    "FAILED", "PARTIAL", "DRIFT", "OVERFLOW", "VAULT_FAILED", "LEARNING", "ROLLBACK", "REPLAY",
)
_EXCEPTION_BIT = {name: 1 << i for i, name in enumerate(EXCEPTIONS)}
# Bits owned by other Phase 8 services (learning / rollback / replay); they are
# never derived from the event and survive a recomputation of the row.
SERVICE_EXCEPTIONS: tuple[str, ...] = ("LEARNING", "ROLLBACK", "REPLAY")

STAGE_BITS = 2
_STAGE_FIELD_BITS = STAGE_BITS * len(STAGES)  # 18
_STAGE_FIELD_MASK = (1 << _STAGE_FIELD_BITS) - 1
_VERSION_SHIFT = 56
_VERSION_MASK = 0x7F
_EXCEPTION_FIELD_MASK = (1 << len(EXCEPTIONS)) - 1
PACKED_BYTES = 5  # version (1) + stages (3, 18 of 24 bits used) + exceptions (1)


class LineageDecodeError(ValueError):
    """The stored compact lineage cannot be decoded without guessing."""


@dataclass(frozen=True)
class CompactLineage:
    template_version: int
    stages: dict[str, str]  # compact stage -> outcome, all nine present
    exceptions: tuple[str, ...]  # in EXCEPTIONS order

    @property
    def is_exception(self) -> bool:
        return bool(self.exceptions) or any(o in (WARN, FAIL) for o in self.stages.values())

    @property
    def worst(self) -> str:
        return OUTCOMES[max(_OUTCOME_CODE[o] for o in self.stages.values())]


def encode_stages(stages: dict[str, str], *, version: int = TEMPLATE_VERSION) -> int:
    if version != TEMPLATE_VERSION:
        raise ValueError(f"Cannot encode template version {version}; this build writes v{TEMPLATE_VERSION}.")
    unknown = set(stages) - set(STAGES)
    if unknown:
        raise ValueError(f"Unknown lineage stage(s): {sorted(unknown)}")
    missing = [s for s in STAGES if s not in stages]
    if missing:
        raise ValueError(f"Missing lineage stage(s): {missing}")
    mask = 0
    for i, name in enumerate(STAGES):
        outcome = stages[name]
        if outcome not in _OUTCOME_CODE:
            raise ValueError(f"Unknown outcome '{outcome}' for stage {name}")
        mask |= _OUTCOME_CODE[outcome] << (STAGE_BITS * i)
    return mask | (version << _VERSION_SHIFT)


def encode_exceptions(exceptions) -> int:
    mask = 0
    for name in exceptions:
        if name not in _EXCEPTION_BIT:
            raise ValueError(f"Unknown lineage exception '{name}'")
        mask |= _EXCEPTION_BIT[name]
    return mask


def exception_bit(name: str) -> int:
    return _EXCEPTION_BIT[name]


SERVICE_EXCEPTION_MASK = encode_exceptions(SERVICE_EXCEPTIONS)


def encode(lineage: CompactLineage) -> tuple[int, int]:
    return encode_stages(lineage.stages, version=lineage.template_version), encode_exceptions(lineage.exceptions)


def decode(stage_mask: int, exception_mask: int, *, template_version: int | None = None) -> CompactLineage:
    if not isinstance(stage_mask, int) or not isinstance(exception_mask, int):
        raise LineageDecodeError("Compact lineage masks must be integers.")
    if stage_mask < 0 or exception_mask < 0:
        raise LineageDecodeError("Compact lineage masks must be non-negative.")
    if stage_mask >> 63:
        raise LineageDecodeError("stage_mask exceeds 63 bits.")
    embedded = (stage_mask >> _VERSION_SHIFT) & _VERSION_MASK
    if template_version is not None and embedded != template_version:
        raise LineageDecodeError(
            f"Template version mismatch: column says v{template_version}, stage_mask embeds v{embedded}.")
    if embedded != TEMPLATE_VERSION:
        raise LineageDecodeError(f"Unsupported lineage template version v{embedded} (this build decodes v{TEMPLATE_VERSION}).")
    reserved = stage_mask & ~(_STAGE_FIELD_MASK | (_VERSION_MASK << _VERSION_SHIFT))
    if reserved:
        raise LineageDecodeError(f"stage_mask has reserved bits set (0x{reserved:x}).")
    if exception_mask & ~_EXCEPTION_FIELD_MASK:
        raise LineageDecodeError(f"exception_mask has reserved bits set (0x{exception_mask & ~_EXCEPTION_FIELD_MASK:x}).")
    stages = {name: OUTCOMES[(stage_mask >> (STAGE_BITS * i)) & 0b11] for i, name in enumerate(STAGES)}
    exceptions = tuple(name for name in EXCEPTIONS if exception_mask & _EXCEPTION_BIT[name])
    return CompactLineage(template_version=embedded, stages=stages, exceptions=exceptions)


def pack(stage_mask: int, exception_mask: int) -> bytes:
    """Wire/storage form: 5 bytes, big-endian (version, stages[3], exceptions)."""
    lineage = decode(stage_mask, exception_mask)  # validates
    stages = stage_mask & _STAGE_FIELD_MASK
    return bytes([lineage.template_version]) + stages.to_bytes(3, "big") + bytes([exception_mask])


def unpack(data: bytes) -> CompactLineage:
    if not isinstance(data, (bytes, bytearray)) or len(data) != PACKED_BYTES:
        raise LineageDecodeError(f"Packed lineage must be exactly {PACKED_BYTES} bytes.")
    version, stages, exceptions = data[0], int.from_bytes(data[1:4], "big"), data[4]
    if stages & ~_STAGE_FIELD_MASK:
        raise LineageDecodeError("Packed lineage has reserved stage bits set.")
    return decode(stages | (version << _VERSION_SHIFT), exceptions)


def from_detailed_chain(chain: list[dict]) -> dict[str, str]:
    """Project a detailed lineage chain (views_service.lineage()['chain']) onto
    the nine compact stages. Raises if a compact stage is absent or repeated."""
    stages: dict[str, str] = {}
    for entry in chain:
        name = DETAILED_TO_COMPACT.get(entry.get("stage"))
        if name is None:
            continue
        if name in stages:
            raise ValueError(f"Stage {entry['stage']} appears twice in the detailed chain.")
        stages[name] = entry["outcome"]
    missing = [s for s in STAGES if s not in stages]
    if missing:
        raise ValueError(f"Detailed chain lacks stage(s): {missing}")
    return stages
