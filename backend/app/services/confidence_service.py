"""AI confidence calibration ledger.

For every parser suggestion (an onboarding proposal version, or a learning
proposal version) this records the *evidence* behind the suggestion's
stated confidence, computed deterministically with the real runtime
pipeline:

- suggestion confidence as stated by the suggester (Claude / offline
  analyzer / human / learning engine);
- sample count, fields observed / mapped / preserved, structural coverage
  and structural diversity (distinct structures among the samples);
- holdout validation: the deterministic offline analyzer is re-run on a
  training split only and its adapter is tested on the held-out samples it
  never saw; the actual proposal is also scored on the same split (the
  suggester saw every sample, so that part is labelled in-sample);
- mutation testing: robustness mutants (value changes, field reordering,
  unknown-field injection) that a sound adapter should still parse, and
  fault mutants (truncation) that should not silently come out SUCCESS;
- the human decision and the eventual production outcome (events later
  normalized by the approved adapter version, by status) — joined in at read
  time from the authoritative records, so they are always current.

This is an evidence ledger and an empirical tally by confidence band. It is
NOT a trained or statistically calibrated model, and nothing here changes
any gate: approval still requires the existing sandbox PASS + a human.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.loader import AdapterRegistry
from app.db.models.event import Event
from app.db.models.governance import ConfidenceLedgerEntry
from app.db.models.learning import LearningSession
from app.db.models.onboarding import OnboardedAdapter, OnboardingSession
from app.onboarding import sandbox
from app.onboarding.analysis import analyze_samples
from app.onboarding.proposal import AdapterProposal, review_proposal, to_adapter
from app.onboarding.providers import SuggestionError, build_offline_proposal, parse_proposal
from app.schema.adapter import AdapterMapping

logger = logging.getLogger(__name__)

ONBOARDING = "ONBOARDING"
LEARNING = "LEARNING"
HOLDOUT_EVERY = 4  # every 4th sample (index % 4 == 3) is held out
MIN_HOLDOUT_SAMPLES = 4
PROBE_ID = "confidence_holdout_probe"
BANDS = ((0.0, 0.5, "<0.50"), (0.5, 0.7, "0.50-0.69"), (0.7, 0.9, "0.70-0.89"), (0.9, 1.01, ">=0.90"))
LEARNING_GRADE_SCORE = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.4}


class ConfidenceNotFound(Exception):
    pass


# --------------------------------------------------------------------------
# Deterministic evaluators
# --------------------------------------------------------------------------


def _rate(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    if metrics is None:
        return None
    return {k: metrics.get(k) for k in ("total_samples", "matched_samples", "failed_samples", "match_rate",
                                         "mapping_coverage", "warning_count")}


def _mutate_digits(raw: str) -> str:
    return re.sub(r"\d", lambda m: str((int(m.group()) + 1) % 10), raw)


def _inject_field(raw: str, parser: dict[str, Any] | None, fmt: str) -> str | None:
    if fmt == "json" or raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, dict):
            data["lf_mutation_probe"] = "1"
            return json.dumps(data)
        return None
    strategy = (parser or {}).get("strategy")
    if strategy == "kv" or fmt == "kv":
        sep = {"whitespace": " "}.get((parser or {}).get("pair_separator", "whitespace"), (parser or {}).get("pair_separator", " "))
        kv = (parser or {}).get("kv_separator", "=")
        return f"{raw}{sep}lf_mutation_probe{kv}1"
    if strategy == "delimited" or fmt == "delimited":
        return f"{raw}{(parser or {}).get('delimiter') or ','}lf_mutation_probe"
    return None


def _reorder_pairs(raw: str, parser: dict[str, Any] | None, fmt: str) -> str | None:
    if (parser or {}).get("strategy") != "kv" and fmt != "kv":
        return None
    if (parser or {}).get("pair_separator", "whitespace") != "whitespace":
        return None
    tokens = raw.split(" ")
    kv = (parser or {}).get("kv_separator", "=")
    pairs = [t for t in tokens if kv in t]
    if len(pairs) < 2:
        return None
    rest = [t for t in tokens if kv not in t]
    return " ".join(rest + list(reversed(pairs)))


def mutation_test(samples: list[str], candidate: AdapterMapping | None, registry: AdapterRegistry,
                  parser: dict[str, Any] | None, fmt: str) -> dict[str, Any]:
    if candidate is None or not samples:
        return {"evaluated": False, "reason": "no valid candidate adapter to mutate against"}
    operators = {
        "value_digit_rotation": ("robustness", [_mutate_digits(s) for s in samples]),
        "field_reorder": ("robustness", [m for m in (_reorder_pairs(s, parser, fmt) for s in samples) if m]),
        "unknown_field_injection": ("robustness", [m for m in (_inject_field(s, parser, fmt) for s in samples) if m]),
        "truncate_half": ("fault", [s[: max(1, len(s) // 2)] for s in samples]),
    }
    results: dict[str, Any] = {}
    for name, (kind, mutants) in operators.items():
        if not mutants:
            results[name] = {"kind": kind, "applicable": False}
            continue
        m = sandbox.run_sandbox(mutants, candidate, registry)
        entry: dict[str, Any] = {"kind": kind, "applicable": True, "mutants": len(mutants),
                                 "matched": m["matched_samples"], "match_rate": m["match_rate"]}
        if kind == "fault":
            # A fault mutant is "detected" unless it is silently matched as a clean SUCCESS.
            silent = sum(1 for r in m["samples"] if r["matched"] and r["status"] == "SUCCESS")
            entry["silently_accepted"] = silent
            entry["detection_rate"] = round(1 - silent / len(mutants), 4)
        results[name] = entry
    robust = [v for v in results.values() if v.get("applicable") and v["kind"] == "robustness"]
    faults = [v for v in results.values() if v.get("applicable") and v["kind"] == "fault"]
    return {
        "evaluated": True,
        "operators": results,
        "robustness_survival_rate": round(sum(v["match_rate"] for v in robust) / len(robust), 4) if robust else None,
        "fault_detection_rate": round(sum(v["detection_rate"] for v in faults) / len(faults), 4) if faults else None,
        "method": "deterministic mutants of the session samples, run through the real pipeline with the candidate adapter",
    }


def holdout_test(samples: list[str], candidate: AdapterMapping | None, registry: AdapterRegistry) -> dict[str, Any]:
    if len(samples) < MIN_HOLDOUT_SAMPLES:
        return {"evaluated": False, "reason": f"fewer than {MIN_HOLDOUT_SAMPLES} samples; no meaningful split"}
    holdout_idx = [i for i in range(len(samples)) if i % HOLDOUT_EVERY == HOLDOUT_EVERY - 1]
    train = [s for i, s in enumerate(samples) if i not in holdout_idx]
    held = [samples[i] for i in holdout_idx]
    out: dict[str, Any] = {"evaluated": True, "split": {"train": len(train), "holdout": len(held),
                                                        "rule": f"index % {HOLDOUT_EVERY} == {HOLDOUT_EVERY - 1} held out"}}
    # (a) Genuine holdout for the deterministic analyzer: derived on train only.
    analysis = analyze_samples(train)
    try:
        proposal = parse_proposal(build_offline_proposal(analysis))
        review = review_proposal(proposal, analysis)
        if review["issues"]:
            out["rederived_offline"] = {"result": "INVALID_ON_TRAIN_SPLIT", "issues": review["issues"][:5]}
        else:
            probe = to_adapter(proposal, review["accepted"], adapter_id=PROBE_ID, version=1,
                               description="confidence ledger holdout probe (never registered)")
            out["rederived_offline"] = {"result": "EVALUATED", "holdout": _rate(sandbox.run_sandbox(held, probe, registry)),
                                        "mappings": len(review["accepted"])}
    except (SuggestionError, ValueError, KeyError) as exc:
        out["rederived_offline"] = {"result": "NOT_DERIVABLE_ON_TRAIN_SPLIT", "detail": str(exc)[:200]}
    # (b) The actual proposal on the same split (the suggester saw every sample: in-sample).
    if candidate is not None:
        out["proposal_on_split"] = {"train": _rate(sandbox.run_sandbox(train, candidate, registry)),
                                    "holdout": _rate(sandbox.run_sandbox(held, candidate, registry)),
                                    "note": "in-sample: the suggester had access to every sample"}
    return out


def _structural(analysis: dict[str, Any], metrics: dict[str, Any] | None, mapped_fields: int) -> dict[str, Any]:
    observed = len(analysis.get("fields") or {})
    return {
        "fields_observed": observed,
        "fields_mapped": mapped_fields,
        "fields_preserved": (metrics or {}).get("unknown_field_count"),
        "structural_coverage": round(mapped_fields / observed, 4) if observed else None,
        "structure_variants": analysis.get("structure_variants"),
        "dominant_structure_share": analysis.get("dominant_structure_share"),
        "structural_consistency": (metrics or {}).get("structural_consistency"),
    }


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def _existing(db: Session, subject_type: str, subject_id: str, version: int) -> ConfidenceLedgerEntry | None:
    return db.execute(select(ConfidenceLedgerEntry).where(
        ConfidenceLedgerEntry.subject_type == subject_type, ConfidenceLedgerEntry.subject_id == subject_id,
        ConfidenceLedgerEntry.proposal_version == version)).scalars().first()


def _insert(db: Session, entry: ConfidenceLedgerEntry) -> ConfidenceLedgerEntry:
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:  # recorded concurrently: keep the first record
        db.rollback()
        return _existing(db, entry.subject_type, entry.subject_id, entry.proposal_version)
    return entry


def record_onboarding(db: Session, session_id: str) -> ConfidenceLedgerEntry | None:
    from app.services import onboarding_service

    session = db.get(OnboardingSession, session_id)
    if session is None:
        raise ConfidenceNotFound(f"Onboarding session '{session_id}' not found")
    if not session.proposal or not session.validation or session.proposal_version < 1:
        return None
    found = _existing(db, ONBOARDING, session.id, session.proposal_version)
    if found is not None:
        return found
    validation = session.validation
    proposal = AdapterProposal.model_validate(session.proposal)
    samples = [s["raw"] for s in session.samples]
    preview = validation.get("adapter_preview")
    candidate = AdapterMapping.model_validate(preview) if preview else None
    registry = onboarding_service.runtime_registry(db)
    metrics = validation.get("metrics")
    mapping_conf = [m.confidence for m in proposal.mappings]
    evidence = {
        "suggestion": {
            "source": session.proposal_source,
            "overall_confidence": proposal.overall_confidence,
            "mean_mapping_confidence": round(sum(mapping_conf) / len(mapping_conf), 4) if mapping_conf else None,
            "mappings_proposed": len(proposal.mappings),
            "mappings_accepted": len(validation.get("accepted_mappings") or []),
            "mappings_rejected": len(validation.get("rejected_mappings") or []),
        },
        "in_sample": {"result": validation.get("result"), **(_rate(metrics) or {})},
        "structural": _structural(session.analysis or {}, metrics, len(validation.get("accepted_mappings") or [])),
        "holdout": holdout_test(samples, candidate, registry),
        "mutation": mutation_test(samples, candidate, registry, (session.proposal or {}).get("parser"), proposal.format),
        "failed_parses": {"samples_not_matched": (metrics or {}).get("failed_samples"),
                          "parse_failures_in_analysis": len((session.analysis or {}).get("parse_failures") or [])},
    }
    return _insert(db, ConfidenceLedgerEntry(
        subject_type=ONBOARDING, subject_id=session.id, proposal_version=session.proposal_version,
        proposal_source=session.proposal_source, adapter_id=(preview or {}).get("id"),
        suggestion_confidence=proposal.overall_confidence, sample_count=len(samples), evidence=evidence))


def record_learning(db: Session, session_id: str) -> ConfidenceLedgerEntry | None:
    from app.services import learning_service, onboarding_service

    session = db.get(LearningSession, session_id)
    if session is None:
        raise ConfidenceNotFound(f"Learning session '{session_id}' not found")
    if not session.proposal or session.proposal_version < 1:
        return None
    found = _existing(db, LEARNING, session.id, session.proposal_version)
    if found is not None:
        return found
    proposal = session.proposal or {}
    validation = session.validation or {}
    grades = [m.get("confidence") for m in (proposal.get("add_mappings") or []) + (proposal.get("remaps") or [])]
    scores = [LEARNING_GRADE_SCORE[g] for g in grades if g in LEARNING_GRADE_SCORE]
    drifted = learning_service._raws(db, session.evidence.get("drifted") or [])
    historical = learning_service._raws(db, session.evidence.get("historical") or [])
    candidate = AdapterMapping.model_validate(session.candidate) if session.candidate else None
    registry = onboarding_service.runtime_registry(db)
    parser = (session.candidate or {}).get("parser")
    new = validation.get("new_structure")
    evidence = {
        "suggestion": {
            "source": session.proposal_source,
            "confidence_grades": {g: grades.count(g) for g in sorted(set(g for g in grades if g))},
            "grade_score": round(sum(scores) / len(scores), 4) if scores else None,
            "grade_score_note": "HIGH=0.9, MEDIUM=0.7, LOW=0.4 — a fixed ordinal mapping, not a probability",
            "risk": session.risk,
            "learning_modes": session.learning_modes,
        },
        "in_sample": {"result": validation.get("result"), **(_rate(new) or {})},
        # Historical events were not used to derive the delta: a genuine holdout for regressions.
        "holdout": {"evaluated": validation.get("historical") is not None,
                    "kind": "historical structures (not used to derive the delta)",
                    "historical": _rate(validation.get("historical")),
                    "regressions": len(validation.get("regressions") or [])},
        "mutation": mutation_test(drifted, candidate, registry, parser, (session.candidate or {}).get("format", "")),
        "structural": {"drifted_samples": len(drifted), "historical_samples": len(historical),
                       "added_fields": len((session.drift.get("differences") or {}).get("added_fields") or []),
                       "removed_fields": len((session.drift.get("differences") or {}).get("removed_fields") or [])},
    }
    return _insert(db, ConfidenceLedgerEntry(
        subject_type=LEARNING, subject_id=session.id, proposal_version=session.proposal_version,
        proposal_source=session.proposal_source, adapter_id=session.source_adapter_id,
        suggestion_confidence=evidence["suggestion"]["grade_score"], sample_count=len(drifted) + len(historical),
        evidence=evidence))


# --------------------------------------------------------------------------
# Reading (human decision + production outcome joined live)
# --------------------------------------------------------------------------


def _production(db: Session, adapter_id: str | None, version: int | None) -> dict[str, Any] | None:
    if not adapter_id or version is None:
        return None
    rows = dict(db.execute(select(Event.status, func.count()).where(
        Event.adapter_id == adapter_id, Event.adapter_version == str(version)).group_by(Event.status)).all())
    total = sum(rows.values())
    return {"adapter_id": adapter_id, "version": version, "events": total, "by_status": rows,
            "success_rate": round(rows.get("SUCCESS", 0) / total, 4) if total else None,
            "measurable": total > 0}


def _human(decisions: list[dict[str, Any]], version: int) -> dict[str, Any] | None:
    for d in reversed(decisions or []):
        if d.get("action") in ("APPROVED", "REJECTED", "ACTIVATED", "ROLLED_BACK"):
            if d.get("proposal_version") in (None, version) or d.get("action") != "APPROVED":
                return {"action": d.get("action"), "by": d.get("by"), "at": d.get("at")}
    return None


def entry_dict(db: Session, e: ConfidenceLedgerEntry) -> dict[str, Any]:
    decision = production = None
    if e.subject_type == ONBOARDING:
        s = db.get(OnboardingSession, e.subject_id)
        if s is not None:
            decision = _human(s.decisions, e.proposal_version)
            if s.status == "APPROVED" and s.proposal_version == e.proposal_version:
                production = _production(db, s.adapter_id, s.adapter_version)
    else:
        s = db.get(LearningSession, e.subject_id)
        if s is not None:
            decision = _human(s.decisions, e.proposal_version)
            if s.target_adapter_row_id and s.proposal_version == e.proposal_version:
                row = db.get(OnboardedAdapter, s.target_adapter_row_id)
                production = _production(db, row.adapter_id, row.version) if row else None
    return {"id": e.id, "subject_type": e.subject_type, "subject_id": e.subject_id,
            "proposal_version": e.proposal_version, "proposal_source": e.proposal_source, "adapter_id": e.adapter_id,
            "suggestion_confidence": e.suggestion_confidence, "sample_count": e.sample_count, "evidence": e.evidence,
            "human_decision": decision, "production_outcome": production, "created_at": e.created_at}


def for_subject(db: Session, subject_type: str, subject_id: str, *, ensure: bool = True) -> list[dict[str, Any]]:
    if ensure:
        (record_onboarding if subject_type == ONBOARDING else record_learning)(db, subject_id)
    rows = db.execute(select(ConfidenceLedgerEntry).where(
        ConfidenceLedgerEntry.subject_type == subject_type, ConfidenceLedgerEntry.subject_id == subject_id)
        .order_by(ConfidenceLedgerEntry.proposal_version)).scalars().all()
    return [entry_dict(db, r) for r in rows]


def calibration(db: Session) -> dict[str, Any]:
    """Empirical tally of historical outcomes per stated-confidence band."""
    bands: dict[str, dict[str, Any]] = {label: {"band": label, "suggestions": 0, "approved": 0, "rejected": 0,
                                                "pending": 0, "production_events": 0, "production_success": 0}
                                        for _, _, label in BANDS}
    unscored = 0
    for e in db.execute(select(ConfidenceLedgerEntry)).scalars().all():
        conf = e.suggestion_confidence
        if conf is None:
            unscored += 1
            continue
        label = next(lbl for lo, hi, lbl in BANDS if lo <= conf < hi)
        d = entry_dict(db, e)
        b = bands[label]
        b["suggestions"] += 1
        action = (d["human_decision"] or {}).get("action")
        if action in ("APPROVED", "ACTIVATED", "ROLLED_BACK"):
            b["approved"] += 1
        elif action == "REJECTED":
            b["rejected"] += 1
        else:
            b["pending"] += 1
        if d["production_outcome"]:
            b["production_events"] += d["production_outcome"]["events"]
            b["production_success"] += d["production_outcome"]["by_status"].get("SUCCESS", 0)
    for b in bands.values():
        decided = b["approved"] + b["rejected"]
        b["approval_rate"] = round(b["approved"] / decided, 4) if decided else None
        b["production_success_rate"] = round(b["production_success"] / b["production_events"], 4) if b["production_events"] else None
    return {"bands": list(bands.values()), "unscored": unscored,
            "method": "Counts of past human decisions and production outcomes grouped by the suggester's stated "
                      "confidence. An empirical tally, not a statistical calibration or a trained model."}
