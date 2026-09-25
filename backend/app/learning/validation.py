"""Learning sandbox: validate a proposed adapter version before approval.

Uses the *real* runtime pipeline (via the Phase 3 sandbox and the
orchestrator) on:
- the drifted events (the new structure must be supported), and
- historical events of previously accepted structures (regression safety:
  the new version must keep normalizing them exactly as the current
  version does).

Decision (deterministic; thresholds reuse the Phase 3 onboarding settings):
- REJECTED      invalid/unsafe delta, drifted match below the rejection
                floor, raw/hash preservation failure, or matched samples
                without a normalized event;
- NEEDS_REVIEW  valid but below a threshold, manual mapping required,
                ambiguous or risky critical-field changes, historical
                compatibility not proven;
- PASSED        everything satisfied — eligible for human approval only.
"""
from __future__ import annotations

from typing import Any

from app.adapters.loader import AdapterRegistry
from app.onboarding import sandbox
from app.pipeline.hashing import sha256_hex
from app.pipeline.orchestrator import process
from app.schema.adapter import AdapterMapping

PASSED, NEEDS_REVIEW, REJECTED = sandbox.PASSED, sandbox.NEEDS_REVIEW, sandbox.REJECTED
NORMALIZED_KEYS = ("network", "user", "process", "event_action", "severity", "event_timestamp")


def validate_candidate(
    candidate: AdapterMapping,
    current_registry: AdapterRegistry,
    drifted: list[str],
    historical: list[str],
    *,
    issues: list[str],
    needs_manual: bool,
    risk_reasons: list[str],
    changes_mapping: bool,
    min_match_rate: float,
    reject_below_match_rate: float,
    min_mapping_coverage: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if issues:
        return {"result": REJECTED, "reasons": [*issues, "The proposal was not executed in the sandbox because it is unsafe."],
                "new_structure": None, "historical": None, "preservation": None, "regressions": [],
                "compatibility_confirmation_required": False}

    new = sandbox.run_sandbox(drifted, candidate, current_registry)
    hist = sandbox.run_sandbox(historical, candidate, current_registry) if historical else None
    candidate_registry = AdapterRegistry([a for a in current_registry.all() if a.id != candidate.id] + [candidate])
    preservation, regressions = _preservation_and_regressions(drifted, historical, candidate, current_registry, candidate_registry)

    result = PASSED
    reasons.append(f"New structure: {new['matched_samples']}/{new['total_samples']} drifted samples parsed by the proposed version.")
    if preservation["raw_or_hash_failures"] or preservation["missing_normalized"]:
        reasons.append(f"Preservation failed: {preservation['raw_or_hash_failures']} raw/hash mismatch(es), "
                       f"{preservation['missing_normalized']} matched sample(s) without a normalized event.")
        return _out(REJECTED, reasons, new, hist, preservation, regressions, False)
    if new["matched_samples"] == 0 or new["match_rate"] < reject_below_match_rate:
        reasons.append(f"Drifted match rate {new['match_rate']:.0%} is below the rejection floor {reject_below_match_rate:.0%}.")
        return _out(REJECTED, reasons, new, hist, preservation, regressions, False)
    reasons.append("Raw events and SHA-256 hashes preserved for every sample; every matched sample produced a normalized event.")

    if new["match_rate"] >= min_match_rate:
        reasons.append(f"Drifted match rate {new['match_rate']:.0%} meets the threshold {min_match_rate:.0%}.")
    else:
        reasons.append(f"Drifted match rate {new['match_rate']:.0%} is below the threshold {min_match_rate:.0%}.")
        result = NEEDS_REVIEW
    if new["mapping_coverage"] < min_mapping_coverage:
        reasons.append(f"Mapping coverage {new['mapping_coverage']:.0%} is below the minimum {min_mapping_coverage:.0%}.")
        result = NEEDS_REVIEW
    if new["warning_count"]:
        reasons.append(f"{new['warning_count']} normalization warning(s) on drifted samples.")
        result = NEEDS_REVIEW

    confirmation = False
    if hist is None:
        reasons.append("No historical events of previously accepted structures are available; backward compatibility is unproven.")
        result = NEEDS_REVIEW
    else:
        regressed = {r["index"] for r in regressions}
        compatible = sum(1 for s in hist["samples"] if s["matched"] and s["index"] not in regressed)
        rate = compatible / hist["total_samples"]
        reasons.append(f"Historical compatibility: {compatible}/{hist['total_samples']} historical samples normalize "
                       f"identically under the proposed version ({rate:.0%}).")
        if rate < min_match_rate:
            reasons.append("Backward compatibility is below the threshold: approving requires explicit confirmation "
                           "that the new version intentionally supersedes the old behavior.")
            result = NEEDS_REVIEW
            confirmation = True
    if needs_manual:
        reasons.append("Some fields need a human mapping decision (meaning uncertain or ambiguous).")
        result = NEEDS_REVIEW
        confirmation = False
    for r in risk_reasons:
        reasons.append(f"Critical-field risk: {r}")
    if any("absent and no replacement" in r for r in risk_reasons):
        result = NEEDS_REVIEW
        confirmation = False
    if not changes_mapping:
        reasons.append("The proposal does not change the adapter; no new version is needed.")
    if result == PASSED:
        reasons.append("Eligible for human approval. Nothing is active until a human approves and activates it.")
    return _out(result, reasons, new, hist, preservation, regressions, confirmation)


def _out(result, reasons, new, hist, preservation, regressions, confirmation) -> dict[str, Any]:
    return {"result": result, "reasons": reasons, "new_structure": new, "historical": hist,
            "preservation": preservation, "regressions": regressions[:50],
            "compatibility_confirmation_required": confirmation}


def _preservation_and_regressions(drifted, historical, candidate, current_registry, candidate_registry):
    raw_failures = 0
    missing_normalized = 0
    for raw in [*drifted, *historical]:
        result = process(raw, adapter_registry=candidate_registry)
        if result.raw_event != raw or result.raw_hash != sha256_hex(raw):
            raw_failures += 1
        if result.adapter_id == candidate.id and result.status != "FAILED" and not result.normalized_event:
            missing_normalized += 1
    regressions: list[dict[str, Any]] = []
    for index, raw in enumerate(historical):
        before = process(raw, adapter_registry=current_registry)
        after = process(raw, adapter_registry=candidate_registry)
        changed = [k for k in NORMALIZED_KEYS if getattr(before, k) != getattr(after, k)]
        if before.adapter_id != after.adapter_id:
            changed.append("adapter")
        if changed:
            regressions.append({"index": index, "changed": changed})
    total = len(drifted) + len(historical)
    return (
        {"samples_checked": total, "raw_or_hash_failures": raw_failures, "missing_normalized": missing_normalized},
        regressions,
    )
