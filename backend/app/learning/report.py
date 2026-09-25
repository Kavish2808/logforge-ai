"""Deterministic, human-readable learning report rendered from the stored
learning session (drift snapshot, evidence, delta, validation, decisions)."""
from __future__ import annotations

from typing import Any


def render_report(s: dict[str, Any]) -> str:
    drift = s.get("drift") or {}
    delta = s.get("proposal") or {}
    validation = s.get("validation") or {}
    evidence = s.get("evidence") or {}
    new = validation.get("new_structure") or {}
    hist = validation.get("historical") or {}
    diff = s.get("mapping_diff") or {}
    lines = [
        f"LEARNING SESSION {s.get('id')} — {s.get('status')}",
        f"Source: {s.get('source_key')}",
        f"Current adapter: {s.get('source_adapter_id')} v{s.get('source_adapter_version')}",
        f"Proposed: {s.get('source_adapter_id')} v{s.get('target_version')}" if s.get("target_version") else "Proposed: no new version",
        f"Trigger: {', '.join(s.get('learning_modes') or []) or 'none'} (drift on event {s.get('trigger_event_id')}, "
        f"severity {drift.get('severity')}, human review: {(drift.get('review') or {}).get('resolution')})",
        f"Evidence: {len(evidence.get('drifted') or [])} drifted event(s), {len(evidence.get('historical') or [])} historical event(s)",
        "",
        "Change:",
    ]
    d = drift.get("differences") or {}
    lines += [f"  + {f}" for f in d.get("added_fields") or []]
    lines += [f"  - {f}" for f in d.get("removed_fields") or []]
    lines += [f"  ~ {f} {c['baseline']} → {c['current']}" for f, c in sorted((d.get("type_changes") or {}).items())]
    if d.get("order_changed"):
        lines.append("  ↕ field order changed")
    lines.append("")
    lines.append("Mapping:")
    lines.append("  (confidence grades the evidence behind each mapping: HIGH/MEDIUM = deterministic engine — "
                 "naming convention, value class, presence in drifted samples; LOW = assistant suggestion. "
                 "Sandbox results are reported separately below.)")
    assisted = {m["raw_field"] for m in ((s.get("assistant") or {}).get("accepted") or [])}
    if assisted:
        lines.append(f"  (assistant-suggested, LOW: {', '.join(sorted(assisted))})")
    for m in delta.get("add_mappings") or []:
        lines.append(f"  + {m['raw_field']} → {m['target']} [{m['confidence']}]")
        lines += [f"      · {e}" for e in m.get("evidence") or []]
    for r in delta.get("remaps") or []:
        lines.append(f"  ⇄ {r['from_field']} → {r['to_field']} (still {r['target']}) [{r['confidence']}]")
        lines += [f"      · {e}" for e in r.get("evidence") or []]
    for r in delta.get("remove_mappings") or []:
        lines.append(f"  - {r['raw_field']} no longer mapped to {r['target']}: {r['reason']}")
    for f in delta.get("optional_fields") or []:
        lines.append(f"  ? {f} kept, now optional")
    if not any(delta.get(k) for k in ("add_mappings", "remaps", "remove_mappings", "optional_fields")):
        lines.append("  (no mapping change)")
    if diff.get("unchanged"):
        lines.append(f"  Unchanged: {len(diff['unchanged'])} mapping(s)")
    for u in delta.get("unresolved") or []:
        lines.append(f"  ! {u['field']} [{u['kind']}]: {u['reason']}")
    for n in delta.get("notes") or []:
        lines.append(f"  note: {n}")
    lines.append("")
    if new:
        lines.append(f"Sandbox: {new.get('matched_samples')}/{new.get('total_samples')} new samples passed; "
                     f"mapping coverage {new.get('mapping_coverage', 0):.0%}")
    if hist:
        regressed = {r['index'] for r in validation.get('regressions') or []}
        ok = sum(1 for x in hist.get("samples") or [] if x["matched"] and x["index"] not in regressed)
        lines.append(f"         {ok}/{hist.get('total_samples')} historical samples normalize identically")
    lines.append(f"Critical fields: {', '.join((s.get('risk_reasons') or [])) or 'none affected'}")
    lines.append(f"Risk: {s.get('risk')}")
    lines.append(f"Validation: {validation.get('result')}")
    lines += [f"  - {r}" for r in validation.get("reasons") or []]
    lines.append(f"Recommendation: {recommendation(s)}")
    return "\n".join(lines)


def recommendation(s: dict[str, Any]) -> str:
    status = s.get("status")
    result = (s.get("validation") or {}).get("result")
    if status == "ACTIVE":
        return "ACTIVE — rollback available"
    if status in ("REJECTED", "ROLLED_BACK", "NO_CHANGE_REQUIRED"):
        return {"REJECTED": "NONE (rejected)", "ROLLED_BACK": "NONE (rolled back)",
                "NO_CHANGE_REQUIRED": "NO_VERSION_NEEDED"}[status]
    if status == "APPROVED":
        return "ACTIVATE"
    if result == "PASSED":
        return "APPROVE_VERSION" if s.get("risk") != "HIGH" else "APPROVE_WITH_CARE (high-risk change)"
    if result == "NEEDS_REVIEW":
        if (s.get("validation") or {}).get("compatibility_confirmation_required"):
            return "REVIEW — approval requires confirming the new version intentionally supersedes old behavior"
        return "REVISE_PROPOSAL (human mapping decision needed)"
    return "DO_NOT_APPROVE"
