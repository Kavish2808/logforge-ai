"""Sandbox validation of a candidate adapter against every sample.

Each sample is run through the *real* runtime pipeline
(app.pipeline.orchestrator.process) with a throwaway registry = the
currently active adapters + the candidate. Nothing is persisted and
nothing is registered; the candidate is pure data interpreted by the
existing parsers/normalizer. The result is therefore exactly what the
runtime would produce after approval — no separate "test parser".

Metrics are deterministic; the decision thresholds are configuration. A
PASSED result means only that the proposal met the configured onboarding
thresholds on these samples — not that it is correct for every future log.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.adapters.loader import AdapterRegistry
from app.pipeline.drift.comparator import compare
from app.pipeline.orchestrator import process
from app.schema.adapter import AdapterMapping
from app.schema.ocsf import EventStatus

PASSED = "PASSED"
NEEDS_REVIEW = "NEEDS_REVIEW"
REJECTED = "REJECTED"


def run_sandbox(samples: list[str], candidate: AdapterMapping, base: AdapterRegistry) -> dict[str, Any]:
    registry = AdapterRegistry([a for a in base.all() if a.id != candidate.id] + [candidate])
    vendor_ids = {a.id for a in base.all() if a.match is not None and a.id != candidate.id}

    results: list[dict[str, Any]] = []
    fingerprints: list[dict[str, Any]] = []
    for index, raw in enumerate(samples):
        result = process(raw, adapter_registry=registry)
        matched = result.adapter_id == candidate.id and result.status != EventStatus.FAILED.value
        fields = (result.structural_fingerprint or {}).get("field_set") or []
        unmapped = sorted(result.extensions.keys()) if matched else []
        entry = {
            "index": index,
            "status": result.status,
            "adapter_id": result.adapter_id,
            "matched": matched,
            "claimed_by_existing": result.adapter_id if result.adapter_id in vendor_ids else None,
            "field_count": len(fields),
            "mapped_field_count": len(fields) - len(unmapped) if matched else 0,
            "unmapped_fields": unmapped,
            "warnings": list(result.warnings) if matched else [],
            "error": result.error_message,
        }
        results.append(entry)
        if matched and result.structural_fingerprint:
            fingerprints.append(result.structural_fingerprint)

    total = len(samples)
    matched = [r for r in results if r["matched"]]
    parsed = [r for r in results if r["status"] != EventStatus.FAILED.value]
    total_fields = sum(r["field_count"] for r in matched)
    mapped_fields = sum(r["mapped_field_count"] for r in matched)
    unknown_fields = sorted({f for r in matched for f in r["unmapped_fields"]})
    claimed = Counter(r["claimed_by_existing"] for r in results if r["claimed_by_existing"])

    return {
        "total_samples": total,
        "parsed_samples": len(parsed),
        "matched_samples": len(matched),
        "failed_samples": total - len(matched),
        "match_rate": round(len(matched) / total, 4) if total else 0.0,
        "mapping_coverage": round(mapped_fields / total_fields, 4) if total_fields else 0.0,
        "unknown_field_count": len(unknown_fields),
        "unknown_fields": unknown_fields,
        "warning_count": sum(len(r["warnings"]) for r in matched),
        "claimed_by_existing": dict(claimed),
        "structural_consistency": _structural_consistency(fingerprints),
        "mapping_presence": _mapping_presence(candidate, fingerprints),
        "samples": results,
    }


def decide(
    review: dict[str, Any],
    metrics: dict[str, Any] | None,
    *,
    min_match_rate: float,
    reject_below_match_rate: float,
    min_mapping_coverage: float,
) -> tuple[str, list[str]]:
    """PASSED / NEEDS_REVIEW / REJECTED plus every reason, in plain words."""
    reasons: list[str] = []
    if review["issues"]:
        reasons.extend(review["issues"])
        reasons.append("The proposal was not executed in the sandbox because it is invalid.")
        return REJECTED, reasons
    assert metrics is not None

    total, matched = metrics["total_samples"], metrics["matched_samples"]
    rate, coverage = metrics["match_rate"], metrics["mapping_coverage"]
    reasons.append(f"{matched}/{total} samples matched the proposed adapter ({metrics['failed_samples']} did not).")

    claimed = metrics["claimed_by_existing"]
    if claimed and sum(claimed.values()) * 2 >= total:
        owners = ", ".join(f"{k} ({v})" for k, v in sorted(claimed.items()))
        reasons.append(
            f"Most samples are already recognized by existing adapters: {owners}. This is a known source — "
            "structural changes to known sources are handled by drift review, not onboarding."
        )
        return REJECTED, reasons
    if matched == 0:
        reasons.append("No sample was parsed by the proposed adapter.")
        return REJECTED, reasons
    if rate < reject_below_match_rate:
        reasons.append(f"Match rate {rate:.0%} is below the rejection floor {reject_below_match_rate:.0%}.")
        return REJECTED, reasons

    result = PASSED
    if rate >= min_match_rate:
        reasons.append(f"Match rate {rate:.0%} meets the onboarding threshold {min_match_rate:.0%}.")
    else:
        reasons.append(f"Match rate {rate:.0%} is below the onboarding threshold {min_match_rate:.0%}.")
        result = NEEDS_REVIEW
    if coverage >= min_mapping_coverage:
        reasons.append(f"Mapping coverage {coverage:.0%} meets the minimum {min_mapping_coverage:.0%}.")
    else:
        reasons.append(f"Mapping coverage {coverage:.0%} is below the minimum {min_mapping_coverage:.0%}.")
        result = NEEDS_REVIEW
    if review["rejected"]:
        reasons.append(
            f"{len(review['rejected'])} proposed mapping(s) were rejected: "
            + "; ".join(r["reason"] for r in review["rejected"][:5])
            + ". Their raw fields stay in extensions."
        )
        result = NEEDS_REVIEW
    if metrics["warning_count"]:
        reasons.append(f"{metrics['warning_count']} normalization warning(s) (type coercion / timestamp) on matched samples.")
        result = NEEDS_REVIEW
    if metrics["unknown_field_count"]:
        reasons.append(
            f"{metrics['unknown_field_count']} field(s) are not mapped and will be preserved in extensions: "
            + ", ".join(metrics["unknown_fields"][:10])
            + ("..." if metrics["unknown_field_count"] > 10 else "")
            + "."
        )
    if result == PASSED:
        reasons.append("Eligible for human approval. Nothing is active until a human approves it.")
    return result, reasons


def _structural_consistency(fingerprints: list[dict[str, Any]]) -> float:
    """Mean Phase 5 structural similarity of each matched sample to the most
    common structure among them (1.0 = every sample has the same shape)."""
    if not fingerprints:
        return 0.0
    signature_counts = Counter(fp["signature"] for fp in fingerprints)
    modal_signature = signature_counts.most_common(1)[0][0]
    modal = next(fp for fp in fingerprints if fp["signature"] == modal_signature)
    scores = [compare(fp, modal, threshold=0.0).similarity for fp in fingerprints]
    return round(sum(scores) / len(scores), 4)


def _mapping_presence(candidate: AdapterMapping, fingerprints: list[dict[str, Any]]) -> dict[str, int]:
    """In how many matched samples each mapped raw field was present."""
    mapped = set(candidate.resolved_field_map())
    for attr in ("event_action_field", "severity_field", "timestamp_field", "product_version_field"):
        value = getattr(candidate, attr)
        if value:
            mapped.add(value)
    return {name: sum(1 for fp in fingerprints if name in fp["field_set"]) for name in sorted(mapped)}
