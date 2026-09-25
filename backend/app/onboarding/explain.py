"""Human-readable onboarding report, rendered from the stored structured
session (analysis, proposal, validation, decisions). Fixed template, no
LLM; every sentence cites evidence counts rather than a bare confidence."""
from __future__ import annotations

from typing import Any


def render_session_explanation(session: dict[str, Any], recommended_samples: int) -> str:
    analysis = session.get("analysis") or {}
    proposal = session.get("proposal") or {}
    validation = session.get("validation") or {}
    metrics = validation.get("metrics") or {}
    count = session.get("sample_count", 0)
    parsed = analysis.get("parsed_in_dominant_format", 0)
    lines = [f"ONBOARDING SESSION {session.get('id')} — {session.get('status')}"]

    note = "" if count >= recommended_samples else f" — fewer than the recommended {recommended_samples}; results are less reliable"
    lines.append(f"Samples: {count}{note}")

    fmt = analysis.get("dominant_format", "unknown")
    lines.append(f"1. Format: {fmt} ({parsed}/{count} samples share this structure; "
                 f"{analysis.get('structure_variants', 0)} structural variant(s)).")

    if not proposal:
        error = session.get("suggestion_error")
        if error:
            lines.append(f"Suggestion failed ({error.get('kind')}): {error.get('message')} Samples are preserved.")
        else:
            lines.append("No proposal yet. Request a suggestion or submit a proposal.")
        lines.append(_activation_line(session, None))
        return "\n".join(lines)

    lines.append(f"2. Suggested vendor/product: {proposal.get('vendor')} / {proposal.get('product')} "
                 f"(source: {session.get('proposal_source')}, proposal v{session.get('proposal_version')}).")
    for reason in (proposal.get("reasoning") or [])[:5]:
        lines.append(f"   - {reason}")

    accepted = validation.get("accepted_mappings") or []
    presence = metrics.get("mapping_presence") or {}
    matched = metrics.get("matched_samples", 0)
    lines.append(f"3. Mapped fields ({len(accepted)}):")
    fields = analysis.get("fields") or {}
    for m in accepted:
        seen = presence.get(m["raw_field"])
        seen_text = f"present in {seen}/{matched} matched samples" if seen is not None else "not observed in sandbox"
        cls = (fields.get(m["raw_field"]) or {}).get("dominant_class")
        cls_text = f", values {cls}" if cls else ""
        lines.append(f"   {m['raw_field']} → {m['target']} ({seen_text}{cls_text}; confidence {m['confidence']})")

    rejected = validation.get("rejected_mappings") or []
    uncertain = list(proposal.get("ambiguous_fields") or [])
    lines.append(f"4. Uncertain: {len(uncertain)} ambiguous field(s)"
                 + (f" ({', '.join(uncertain[:10])})" if uncertain else "")
                 + f"; {len(rejected)} rejected mapping(s)"
                 + (": " + "; ".join(r["reason"] for r in rejected[:5]) if rejected else "") + ".")
    for assumption in (proposal.get("assumptions") or [])[:5]:
        lines.append(f"   - assumption: {assumption}")
    optional = analysis.get("optional_fields") or []
    if optional:
        lines.append(f"   - {len(optional)} optional field(s) appear in only some samples: {', '.join(optional[:10])}.")

    if metrics:
        lines.append(f"5. Sandbox: {metrics['matched_samples']}/{metrics['total_samples']} samples matched the "
                     f"proposed adapter; {metrics['failed_samples']} did not.")
        lines.append(f"6. Match rate {metrics['match_rate']:.0%}; mapping coverage {metrics['mapping_coverage']:.0%}; "
                     f"{metrics['unknown_field_count']} unmapped field(s) preserved in extensions; "
                     f"structural consistency {metrics['structural_consistency']:.2f}.")
    else:
        lines.append("5-6. Sandbox not run: the proposal was invalid.")
    lines.append(f"7. Validation: {validation.get('result')}")
    for reason in validation.get("reasons") or []:
        lines.append(f"   - {reason}")
    lines.append(_activation_line(session, validation))
    return "\n".join(lines)


def _activation_line(session: dict[str, Any], validation: dict[str, Any] | None) -> str:
    status = session.get("status")
    if status == "APPROVED":
        return (f"8. ACTIVE: approved as adapter '{session.get('adapter_id')}' v{session.get('adapter_version')}; "
                "matching logs are now parsed by the normal pipeline without any LLM call.")
    if status == "REJECTED":
        return "8. NOT ACTIVE: rejected by a human reviewer. Samples are preserved; a new proposal can be submitted."
    if not validation:
        return "8. NOT ACTIVE."
    preview = validation.get("adapter_preview") or {}
    parser = preview.get("parser") or {"strategy": "native"}
    match = preview.get("match") or {}
    rule = f"{match.get('field')} {'equals' if match.get('equals') is not None else 'contains'} " \
           f"'{match.get('equals') if match.get('equals') is not None else match.get('contains')}'"
    what = (f"adapter '{preview.get('id')}' v{preview.get('version')} ({preview.get('format')}, "
            f"{parser.get('strategy')} parser), recognized when {rule}; "
            f"{len(validation.get('accepted_mappings') or [])} field mapping(s); all other fields preserved in extensions")
    if validation.get("result") == "PASSED":
        return f"8. NOT ACTIVE — awaiting human approval. On approval: {what}."
    return f"8. NOT ACTIVE — not eligible for approval ({validation.get('result')}). Would activate: {what}."
