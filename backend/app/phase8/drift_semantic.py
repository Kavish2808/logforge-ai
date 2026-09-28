"""Phase 8 Step 4: semantic drift *advisories* (pure, DB-free, deterministic).

These are explainable heuristics, not semantic understanding. They only run
when the statistical layer already produced a finding for the field, and
their output is always advisory (never gates, never changes state). No LLM,
no network.

ACTION_RELABEL_ADVISORY
    event_action changed materially (statistical finding) while the
    structure and the related normalized fields (severity, protocol, port,
    direction) stayed statistically stable. Vanished and new action values
    are paired when the context they occur in is nearly identical — a
    possible vendor relabeling (e.g. "deny" -> "blocked") rather than a
    behavior change.

SEVERITY_REMAP_ADVISORY
    severity changed materially while event_action and the structure stayed
    stable. For actions frequent in both windows, the most common severity
    is compared — the same actions reported with different severities points
    to a changed severity mapping rather than different activity.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

CONTEXT_FIELDS: tuple[str, ...] = ("severity", "network.protocol", "network.dst_port", "network.direction")
PAIR_MIN_SHARE = 0.02
PAIR_MIN_CONTEXT_SIMILARITY = 0.80
PAIR_MIN_SHARE_RATIO = 0.50
REMAP_MIN_SHARE = 0.05
MAX_PAIRS = 20
MIN_SIGNATURE_COVERAGE = 0.95

RELABEL = "ACTION_RELABEL_ADVISORY"
REMAP = "SEVERITY_REMAP_ADVISORY"


@dataclass
class Advisory:
    field: str
    metric: str
    baseline_value: dict[str, Any]
    current_value: dict[str, Any]
    deviation: float
    threshold: float
    explanation: str
    evidence: dict[str, Any]


def structure_stable(base_signatures: list[str | None], cur_signatures: list[str | None],
                     cur_structural_drift: int) -> tuple[bool, dict[str, Any]]:
    """Structure is 'stable' when no current event was flagged by Phase 5 and
    >= 95% of current events have a structural signature seen in the baseline."""
    known = {s for s in base_signatures if s}
    covered = sum(1 for s in cur_signatures if s in known)
    coverage = covered / len(cur_signatures) if cur_signatures else 0.0
    stable = cur_structural_drift == 0 and coverage >= MIN_SIGNATURE_COVERAGE
    return stable, {"signature_coverage": round(coverage, 6), "phase5_drift_events_current": cur_structural_drift,
                    "baseline_signatures": len(known), "min_signature_coverage": MIN_SIGNATURE_COVERAGE}


def _context_distribution(rows: list[dict[str, Any]], action: str) -> Counter:
    return Counter(tuple(r.get(f) for f in CONTEXT_FIELDS) for r in rows if r.get("event_action") == action)


def _similarity(a: Counter, b: Counter) -> float:
    """1 - total variation distance between two context distributions."""
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return 0.0
    keys = set(a) | set(b)
    return 1 - 0.5 * sum(abs(a[k] / na - b[k] / nb) for k in keys)


def _shares(rows: list[dict[str, Any]], name: str) -> dict[str, float]:
    values = [r.get(name) for r in rows if r.get(name) is not None]
    counts = Counter(values)
    return {v: n / len(values) for v, n in counts.items()} if values else {}


def action_relabel(base_rows: list[dict[str, Any]], cur_rows: list[dict[str, Any]], *,
                   stable_fields: set[str], structure: tuple[bool, dict[str, Any]]) -> Advisory | None:
    stable, structure_evidence = structure
    unstable_context = [f for f in CONTEXT_FIELDS if f not in stable_fields]
    if not stable or unstable_context:
        return None
    bs, cs = _shares(base_rows, "event_action"), _shares(cur_rows, "event_action")
    vanished = sorted(v for v, s in bs.items() if v not in cs and s >= PAIR_MIN_SHARE)
    new = sorted(v for v, s in cs.items() if v not in bs and s >= PAIR_MIN_SHARE)
    candidates = []
    for old in vanished:
        old_ctx = _context_distribution(base_rows, old)
        for nv in new:
            sim = _similarity(old_ctx, _context_distribution(cur_rows, nv))
            ratio = min(bs[old], cs[nv]) / max(bs[old], cs[nv])
            if sim >= PAIR_MIN_CONTEXT_SIMILARITY and ratio >= PAIR_MIN_SHARE_RATIO:
                candidates.append((-round(sim, 6), -round(ratio, 6), old, nv))
    used_old, used_new, pairs = set(), set(), []
    for neg_sim, neg_ratio, old, nv in sorted(candidates):
        if old in used_old or nv in used_new:
            continue
        used_old.add(old)
        used_new.add(nv)
        pairs.append({"baseline_value": old, "current_value": nv, "context_similarity": -neg_sim,
                      "share_ratio": -neg_ratio, "baseline_share": round(bs[old], 6), "current_share": round(cs[nv], 6)})
    pairs = pairs[:MAX_PAIRS]
    if not pairs:
        return None
    best = max(p["context_similarity"] for p in pairs)
    described = ", ".join(f"'{p['baseline_value']}' -> '{p['current_value']}'" for p in pairs[:5])
    return Advisory(
        field="event_action", metric=RELABEL,
        baseline_value={"vanished_values": vanished[:MAX_PAIRS]}, current_value={"new_values": new[:MAX_PAIRS]},
        deviation=round(best, 6), threshold=PAIR_MIN_CONTEXT_SIMILARITY,
        explanation=(
            f"event_action changed materially while the structure (signature coverage "
            f"{structure_evidence['signature_coverage']:.1%}, no Phase 5 drift) and the related fields "
            f"({', '.join(CONTEXT_FIELDS)}) stayed statistically stable. {len(pairs)} vanished value(s) occur in "
            f"nearly the same context as a new value ({described}). This may be a vendor relabeling of the same "
            f"activity rather than new behavior. Advisory only: verify against the vendor's change notes."),
        evidence={"pairs": pairs, "structure": structure_evidence, "stable_context_fields": sorted(stable_fields),
                  "rules": {"min_share": PAIR_MIN_SHARE, "min_context_similarity": PAIR_MIN_CONTEXT_SIMILARITY,
                            "min_share_ratio": PAIR_MIN_SHARE_RATIO,
                            "context_similarity": "1 - total variation distance over (severity, protocol, dst_port, direction)"}},
    )


def _modal(rows: list[dict[str, Any]], action: str) -> tuple[str | None, int]:
    counts = Counter(r.get("severity") for r in rows if r.get("event_action") == action and r.get("severity") is not None)
    if not counts:
        return None, 0
    value = sorted(counts, key=lambda v: (-counts[v], v))[0]
    return value, sum(counts.values())


def severity_remap(base_rows: list[dict[str, Any]], cur_rows: list[dict[str, Any]], *,
                   action_stable: bool, structure: tuple[bool, dict[str, Any]]) -> Advisory | None:
    stable, structure_evidence = structure
    if not stable or not action_stable:
        return None
    bs, cs = _shares(base_rows, "event_action"), _shares(cur_rows, "event_action")
    common = sorted(v for v in bs if v in cs and bs[v] >= REMAP_MIN_SHARE and cs[v] >= REMAP_MIN_SHARE)
    changes, compared = [], []
    for action in common:
        (b, bn), (c, cn) = _modal(base_rows, action), _modal(cur_rows, action)
        if b is None or c is None:
            continue
        compared.append(action)
        if b != c:
            changes.append({"event_action": action, "baseline_modal_severity": b, "current_modal_severity": c,
                            "baseline_events": bn, "current_events": cn})
    if not changes:
        return None
    fraction = len(changes) / len(compared)
    described = ", ".join(f"'{x['event_action']}': {x['baseline_modal_severity']} -> {x['current_modal_severity']}"
                          for x in changes[:5])
    return Advisory(
        field="severity", metric=REMAP,
        baseline_value={"actions_compared": compared}, current_value={"actions_with_changed_severity": len(changes)},
        deviation=round(fraction, 6), threshold=0.0,
        explanation=(
            f"severity changed materially while event_action and the structure stayed stable. For {len(changes)} of "
            f"{len(compared)} frequent action(s) the most common severity changed ({described}). The same activity "
            f"now carries a different severity: possibly a changed severity mapping at the source. Advisory only."),
        evidence={"changes": changes, "structure": structure_evidence, "min_action_share": REMAP_MIN_SHARE},
    )
