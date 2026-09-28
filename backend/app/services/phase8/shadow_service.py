"""Phase 8 Step 6: stratified shadow validation + circuit breaker.

Before a learned adapter revision is activated, real stored events are run
through the real pipeline twice — OLD (the runtime registry as it is) and NEW
(the same registry with the candidate in place of the source's adapter) — in
a throwaway context:

- registries are built in memory; the production adapter is never replaced;
- Phase 5 drift classification runs inside a SAVEPOINT that is always rolled
  back, so a baseline it would bootstrap is never written;
- stored events are only read. Nothing but the shadow_runs row is written.

Six deterministic, disjoint strata (precedence in this order), newest first,
ties by event_id, 25 events each (fewer = all available, reported as
insufficient — never padded):

    FAILED           status FAILED (any source: the candidate must not start
                     claiming or breaking malformed input)
    DRIFT            Phase 5 DRIFT / POSSIBLE_FORMAT_DRIFT for this source
    PARTIAL_WARNING  this source, PARTIAL or with warnings
    EXTENSION_HEAVY  this source, more extension keys than the source median, or spilled
    NORMAL           this source, everything else
    DIVERSITY        other sources, round-robin by adapter (hijack check)

Circuit breaker (policy BLOCK_CRITICAL) -> BLOCKED:
    STATUS_DEGRADED     pipeline status got worse (SUCCESS -> PARTIAL/FAILED, PARTIAL -> FAILED)
    EVIDENCE_LOSS       a parsed field accounted for by OLD (mapped or preserved) is
                        not accounted for by NEW
    RAW_HASH_MISMATCH   NEW output's raw bytes / SHA-256 differ from the stored event
    LATENCY_P95         NEW p95 > 3 x OLD p95 (and at least LATENCY_FLOOR_MS slower)

Otherwise REVIEW_REQUIRED when a stratum is under-covered or a non-critical
difference exists (value changed, mapping demoted, adapter changed, warnings
added, drift classification changed); PASSED only with full coverage and no
such difference. Improvements (field newly mapped / promoted from
extensions, status improved, warnings removed) are recorded but never block.
"""
from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, literal_column, not_, or_, select
from sqlalchemy.orm import Session

from app.adapters.loader import AdapterRegistry
from app.core.ids import generate_event_id
from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.db.models.phase8 import (
    SHADOW_BLOCKED,
    SHADOW_PASSED,
    SHADOW_REVIEW_REQUIRED,
    SHADOW_RUNNING,
    ShadowRun,
)
from app.evidence.canonical import canonical_sha256
from app.pipeline.hashing import sha256_hex
from app.pipeline.orchestrator import process as run_pipeline
from app.schema.adapter import AdapterMapping
from app.services import drift_service, ingestion_service, onboarding_service, views_service

logger = logging.getLogger(__name__)

STRATA: tuple[str, ...] = ("FAILED", "DRIFT", "PARTIAL_WARNING", "EXTENSION_HEAVY", "NORMAL", "DIVERSITY")
STRATUM_TARGET = 25
LATENCY_RATIO = 3.0
LATENCY_FLOOR_MS = 0.5
LATENCY_REPEATS = 3
MAX_DIFF_ENTRIES = 200
MAX_CHANGES_PER_EVENT = 20
POLICY = "BLOCK_CRITICAL"
CRITICAL = ("STATUS_DEGRADED", "EVIDENCE_LOSS", "RAW_HASH_MISMATCH")
REVIEW = ("VALUE_CHANGED", "MAPPING_DEMOTED", "ADAPTER_CHANGED", "WARNINGS_ADDED", "EXTENSION_VALUE_CHANGED",
          "DRIFT_CLASSIFICATION_CHANGED")
IMPROVEMENT = ("STATUS_IMPROVED", "NEWLY_MAPPED", "EXTENSION_PROMOTED", "WARNINGS_REMOVED")
_STATUS_RANK = {"SUCCESS": 0, "PARTIAL": 1, "FAILED": 2}
_COMPARED_SCALARS = ("format_detected", "vendor", "product", "product_version", "ocsf_class_uid", "ocsf_category_uid",
                     "event_type", "event_action", "severity", "severity_id", "event_timestamp")

_DRIFT = Event.processing_metadata["drift"]
_DRIFT_STATUS = _DRIFT["status"].astext
_DRIFT_SOURCE = _DRIFT["source_key"].astext
_EXT_KEYS = func.coalesce(func.jsonb_array_length(
    func.jsonb_path_query_array(Event.extensions, literal_column("'$.keyvalue()'::jsonpath"))), 0)
_SPILLED = Event.processing_metadata["extension_spill"]["mode"].astext == "SPILLED"
_HAS_WARNINGS = func.jsonb_array_length(Event.warnings) > 0


class ShadowError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------
# Strata
# --------------------------------------------------------------------------


def extension_median(db: Session, source: str) -> int:
    """Median extension-key count of the source's processed events: the
    source's own notion of 'typical', since unmapped-field counts differ per vendor."""
    value = db.execute(select(func.percentile_disc(0.5).within_group(_EXT_KEYS))
                       .where(Event.adapter_id == source, Event.status != "FAILED")).scalar()
    return int(value or 0)


def _conditions(source: str, median_keys: int) -> dict[str, Any]:
    own = Event.adapter_id == source
    failed = Event.status == "FAILED"
    drift = and_(_DRIFT_SOURCE == source, _DRIFT_STATUS.in_(("DRIFT", "POSSIBLE_FORMAT_DRIFT")))
    partial = and_(own, or_(Event.status == "PARTIAL", _HAS_WARNINGS))
    heavy = and_(own, or_(_EXT_KEYS > median_keys, _SPILLED))
    earlier = []
    out = {}
    for name, raw in (("FAILED", failed), ("DRIFT", drift), ("PARTIAL_WARNING", partial),
                      ("EXTENSION_HEAVY", heavy), ("NORMAL", own)):
        # NULL-safe: missing JSON keys make a predicate NULL, and NOT NULL would drop the row everywhere.
        cond = func.coalesce(raw, False)
        out[name] = and_(cond, *[not_(c) for c in earlier]) if earlier else cond
        earlier.append(cond)
    out["DIVERSITY"] = and_(Event.adapter_id.is_not(None), Event.adapter_id != source, not_(failed),
                            not_(func.coalesce(drift, False)))
    return out


def select_strata(db: Session, source: str, target: int | None = None) -> dict[str, dict[str, Any]]:
    target = target or STRATUM_TARGET
    order = (Event.received_at.desc(), Event.event_id.desc())
    result: dict[str, dict[str, Any]] = {}
    median_keys = extension_median(db, source)
    for name, cond in _conditions(source, median_keys).items():
        available = db.execute(select(func.count()).select_from(Event).where(cond)).scalar_one()
        if name == "DIVERSITY":
            ranked = select(Event.event_id, Event.adapter_id, Event.received_at,
                            func.row_number().over(partition_by=Event.adapter_id, order_by=order).label("rn")
                            ).where(cond).subquery()
            ids = db.execute(select(ranked.c.event_id).order_by(ranked.c.rn, ranked.c.adapter_id,
                                                                ranked.c.received_at.desc(), ranked.c.event_id.desc())
                             .limit(target)).scalars().all()
        else:
            ids = db.execute(select(Event.event_id).where(cond).order_by(*order).limit(target)).scalars().all()
        result[name] = {"target": target, "available": available, "selected": list(ids),
                        "sufficient": len(ids) >= target}
    result["EXTENSION_HEAVY"]["rule"] = f"more than {median_keys} extension keys (source median) or spilled"
    return result


# --------------------------------------------------------------------------
# One side of the comparison
# --------------------------------------------------------------------------


def _process(raw: str, received_at: datetime, registry: AdapterRegistry) -> dict[str, Any]:
    """The real deterministic pipeline, exactly as ingestion/reprocess runs it."""
    return ingestion_service._pipeline_result_fields(run_pipeline(raw, received_at=received_at, adapter_registry=registry))


def _timed(raw: str, received_at: datetime, registry: AdapterRegistry) -> tuple[dict[str, Any], float]:
    t = time.perf_counter()
    fields = _process(raw, received_at, registry)
    return fields, (time.perf_counter() - t) * 1000


def _drift_status(db: Session, fields: dict[str, Any], event_id: str, registry: AdapterRegistry) -> str | None:
    """Phase 5 classification in a throwaway savepoint (a bootstrap it would write is rolled back)."""
    probe = {**fields, "processing_metadata": dict(fields.get("processing_metadata") or {})}
    savepoint = db.begin_nested()
    try:
        drift_service.evaluate(db, probe, event_id, adapter_registry=registry)
    finally:
        savepoint.rollback()
    return ((probe.get("processing_metadata") or {}).get("drift") or {}).get("status")


def _targets(fields: dict[str, Any]) -> dict[str, Any]:
    out = {k: fields.get(k) for k in _COMPARED_SCALARS}
    for group in ("network", "user", "process"):
        for key, value in (fields.get(group) or {}).items():
            out[f"{group}.{key}"] = value
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in out.items()}


def _accounted(fields: dict[str, Any], registry: AdapterRegistry) -> tuple[set[str], list[str]]:
    if fields.get("status") == "FAILED":
        return set(), []
    parsed = list((fields.get("structural_fingerprint") or {}).get("field_order") or [])
    mapping = registry.get(fields["adapter_id"]) if fields.get("adapter_id") else None
    transient = Event(**{k: v for k, v in fields.items() if k != "received_at"})
    acc = views_service._field_accounting(transient, parsed, dict(fields.get("extensions") or {}), mapping, {})
    return {f["field"] for f in acc["fields"] if f["outcome"] != "UNACCOUNTED"}, parsed


def compare_event(old: dict[str, Any], new: dict[str, Any], stored: Event, old_reg: AdapterRegistry,
                  new_reg: AdapterRegistry, old_drift: str | None, new_drift: str | None) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(kind, **detail):
        changes.append({"kind": kind, **detail})

    if new.get("raw_event") != stored.raw_event or sha256_hex(new.get("raw_event") or "") != stored.raw_hash:
        add("RAW_HASH_MISMATCH", stored=stored.raw_hash, candidate=sha256_hex(new.get("raw_event") or ""))
    ro, rn = _STATUS_RANK.get(old.get("status"), 2), _STATUS_RANK.get(new.get("status"), 2)
    if rn > ro:
        add("STATUS_DEGRADED", old=old.get("status"), new=new.get("status"))
    elif rn < ro:
        add("STATUS_IMPROVED", old=old.get("status"), new=new.get("status"))
    old_acc, _ = _accounted(old, old_reg)
    new_acc, _ = _accounted(new, new_reg)
    lost = sorted(old_acc - new_acc)
    if lost:
        add("EVIDENCE_LOSS", fields=lost[:50])
    if old.get("adapter_id") != new.get("adapter_id"):
        add("ADAPTER_CHANGED", old=old.get("adapter_id"), new=new.get("adapter_id"))
    ot, nt = _targets(old), _targets(new)
    for key in sorted(set(ot) | set(nt)):
        o, n = ot.get(key), nt.get(key)
        if o == n:
            continue
        if o is None:
            add("NEWLY_MAPPED", target=key, new=n)
        elif n is None:
            add("MAPPING_DEMOTED", target=key, old=o)
        else:
            add("VALUE_CHANGED", target=key, old=o, new=n)
    oe, ne = old.get("extensions") or {}, new.get("extensions") or {}
    for key in sorted(set(oe) | set(ne)):
        if key in oe and key not in ne:
            add("EXTENSION_PROMOTED", key=key)  # accounted elsewhere (else EVIDENCE_LOSS above)
        elif key in oe and key in ne and oe[key] != ne[key]:
            add("EXTENSION_VALUE_CHANGED", key=key)
    ow, nw = set(old.get("warnings") or []), set(new.get("warnings") or [])
    if nw - ow:
        add("WARNINGS_ADDED", warnings=sorted(nw - ow)[:10])
    if ow - nw:
        add("WARNINGS_REMOVED", warnings=sorted(ow - nw)[:10])
    if old_drift != new_drift:
        add("DRIFT_CLASSIFICATION_CHANGED", old=old_drift, new=new_drift)
    return changes


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))], 4)


# --------------------------------------------------------------------------
# Shadow comparison (no writes)
# --------------------------------------------------------------------------


def registries(db: Session, source: str, candidate: AdapterMapping) -> tuple[AdapterRegistry, AdapterRegistry]:
    current = onboarding_service.runtime_registry(db).all()
    old = AdapterRegistry(list(current))
    if not any(a.id == source for a in current):
        raise ShadowError(409, f"Source '{source}' has no adapter in the runtime registry to compare against.")
    new = AdapterRegistry([candidate if a.id == source else a for a in current])
    return old, new


def compare(db: Session, source: str, candidate: AdapterMapping, *, target: int | None = None) -> dict[str, Any]:
    old_reg, new_reg = registries(db, source, candidate)
    strata = select_strata(db, source, target)
    old_lat: list[float] = []
    new_lat: list[float] = []
    per_stratum: dict[str, dict[str, Any]] = {}
    diffs: list[dict[str, Any]] = []
    totals = {"status_changes": 0, "evidence_loss": 0, "raw_hash_mismatches": 0, "drift_differences": 0,
              "old_parse_success": 0, "new_parse_success": 0, "review_differences": 0, "improvements": 0}
    for name in STRATA:
        stats = {"events": 0, "old_parse_success": 0, "new_parse_success": 0, "critical": 0, "review": 0,
                 "improvements": 0, "unchanged": 0}
        ids = strata[name]["selected"]
        rows = {e.event_id: e for e in db.execute(select(Event).where(Event.event_id.in_(ids))).scalars()} if ids else {}
        for event_id in ids:
            stored = rows[event_id]
            o_times, n_times = [], []
            for _ in range(LATENCY_REPEATS):  # interleaved, median per event
                old, t_old = _timed(stored.raw_event, stored.received_at, old_reg)
                new, t_new = _timed(stored.raw_event, stored.received_at, new_reg)
                o_times.append(t_old)
                n_times.append(t_new)
            old_lat.append(statistics.median(o_times))
            new_lat.append(statistics.median(n_times))
            old_drift = _drift_status(db, old, event_id, old_reg) if old.get("status") != "FAILED" else None
            new_drift = _drift_status(db, new, event_id, new_reg) if new.get("status") != "FAILED" else None
            changes = compare_event(old, new, stored, old_reg, new_reg, old_drift, new_drift)
            kinds = {c["kind"] for c in changes}
            stats["events"] += 1
            stats["old_parse_success"] += old.get("status") != "FAILED"
            stats["new_parse_success"] += new.get("status") != "FAILED"
            stats["critical"] += bool(kinds & set(CRITICAL))
            stats["review"] += bool(kinds & set(REVIEW))
            stats["improvements"] += bool(kinds & set(IMPROVEMENT))
            stats["unchanged"] += not changes
            totals["status_changes"] += bool(kinds & {"STATUS_DEGRADED", "STATUS_IMPROVED"})
            totals["evidence_loss"] += "EVIDENCE_LOSS" in kinds
            totals["raw_hash_mismatches"] += "RAW_HASH_MISMATCH" in kinds
            totals["drift_differences"] += "DRIFT_CLASSIFICATION_CHANGED" in kinds
            totals["review_differences"] += bool(kinds & set(REVIEW))
            totals["improvements"] += bool(kinds & set(IMPROVEMENT))
            if changes and len(diffs) < MAX_DIFF_ENTRIES:
                diffs.append({"event_id": event_id, "stratum": name, "raw_hash": stored.raw_hash,
                              "old": {"status": old.get("status"), "adapter": old.get("adapter_id"),
                                      "version": old.get("adapter_version"), "drift": old_drift},
                              "new": {"status": new.get("status"), "adapter": new.get("adapter_id"),
                                      "version": new.get("adapter_version"), "drift": new_drift},
                              "changes": changes[:MAX_CHANGES_PER_EVENT]})
        totals["old_parse_success"] += stats["old_parse_success"]
        totals["new_parse_success"] += stats["new_parse_success"]
        per_stratum[name] = stats
    latency = {"old": {"p50": _pct(old_lat, 0.5), "p95": _pct(old_lat, 0.95)},
               "new": {"p50": _pct(new_lat, 0.5), "p95": _pct(new_lat, 0.95)},
               "repeats_per_event": LATENCY_REPEATS, "unit": "ms"}
    return {"strata": strata, "per_stratum": per_stratum, "totals": totals, "diffs": diffs, "latency": latency,
            "sample_count": sum(len(s["selected"]) for s in strata.values())}


def verdict(result: dict[str, Any]) -> tuple[str, bool, list[dict[str, Any]]]:
    reasons: list[dict[str, Any]] = []
    t = result["totals"]

    def events(kind):
        return [d["event_id"] for d in result["diffs"] if any(c["kind"] == kind for c in d["changes"])][:25]

    for kind, key in (("STATUS_DEGRADED", None), ("EVIDENCE_LOSS", "evidence_loss"),
                      ("RAW_HASH_MISMATCH", "raw_hash_mismatches")):
        ids = events(kind)
        if ids:
            reasons.append({"code": kind, "critical": True, "count": t[key] if key else len(ids), "event_ids": ids,
                            "detail": {"STATUS_DEGRADED": "candidate processing status is worse than current",
                                       "EVIDENCE_LOSS": "fields accounted for today would no longer be accounted for",
                                       "RAW_HASH_MISMATCH": "candidate output altered the raw event"}[kind]})
    old95, new95 = result["latency"]["old"]["p95"], result["latency"]["new"]["p95"]
    if old95 is not None and new95 is not None and new95 > LATENCY_RATIO * old95 and new95 - old95 >= LATENCY_FLOOR_MS:
        reasons.append({"code": "LATENCY_P95", "critical": True, "old_p95_ms": old95, "new_p95_ms": new95,
                        "detail": f"candidate p95 {new95} ms > {LATENCY_RATIO} x current p95 {old95} ms"})
    if reasons:
        return SHADOW_BLOCKED, True, reasons
    thin = {n: s for n, s in result["strata"].items() if not s["sufficient"]}
    if thin:
        reasons.append({"code": "INSUFFICIENT_COVERAGE", "critical": False,
                        "strata": {n: {"selected": len(s["selected"]), "target": s["target"]} for n, s in thin.items()},
                        "detail": "some strata have fewer events than the target; success is not claimed"})
    if t["review_differences"]:
        kinds = sorted({c["kind"] for d in result["diffs"] for c in d["changes"] if c["kind"] in REVIEW})
        reasons.append({"code": "NON_CRITICAL_DIFFERENCES", "critical": False, "count": t["review_differences"],
                        "kinds": kinds, "detail": "differences that need a human decision (see diff)"})
    return (SHADOW_REVIEW_REQUIRED if reasons else SHADOW_PASSED), False, reasons


# --------------------------------------------------------------------------
# Persisted runs for learning sessions
# --------------------------------------------------------------------------


def candidate_sha(candidate: dict[str, Any] | None) -> str | None:
    return canonical_sha256(candidate) if candidate else None


def run_for_session(db: Session, session_id: str, *, actor: str) -> ShadowRun:
    session = db.get(LearningSession, session_id)
    if session is None:
        raise ShadowError(404, f"Learning session '{session_id}' not found")
    if not session.candidate:
        raise ShadowError(409, f"Learning session '{session_id}' has no candidate adapter to validate "
                               f"(status {session.status}).")
    candidate = AdapterMapping.model_validate({**session.candidate, "source": "onboarded"})
    started = datetime.now(tz=timezone.utc)
    run = ShadowRun(
        id=generate_event_id(), learning_session_id=session.id, source_key=session.source_key,
        proposal_version=session.proposal_version, old_version=str(session.source_adapter_version),
        new_version=str(session.target_version) if session.target_version else candidate.version,
        strata={}, summary={"status": SHADOW_RUNNING, "started_at": started.isoformat(),
                            "candidate_sha256": candidate_sha(session.candidate)},
        diff=[], sample_count=0, verdict=SHADOW_RUNNING, breaker_tripped=False, reasons=[],
        thresholds={"policy": POLICY, "stratum_target": STRATUM_TARGET, "latency_ratio": LATENCY_RATIO,
                    "latency_floor_ms": LATENCY_FLOOR_MS, "critical": list(CRITICAL) + ["LATENCY_P95"]},
        latency={}, created_by=actor[:128],
    )
    db.add(run)
    db.commit()  # RUNNING is durable before any work starts
    try:
        result = compare(db, session.source_key, candidate)
        final, tripped, reasons = verdict(result)
    except Exception as exc:  # noqa: BLE001 — fail closed: an unvalidated candidate is never PASSED
        db.rollback()
        logger.exception("Shadow run %s failed.", run.id)
        result, final, tripped = None, SHADOW_BLOCKED, True
        reasons = [{"code": "SHADOW_ERROR", "critical": True, "detail": f"shadow validation failed ({type(exc).__name__})"}]
    run = db.get(ShadowRun, run.id)
    finished = datetime.now(tz=timezone.utc)
    if result is not None:
        run.strata = {n: {**s, "results": result["per_stratum"][n]} for n, s in result["strata"].items()}
        run.diff = result["diffs"]
        run.sample_count = result["sample_count"]
        run.latency = result["latency"]
    run.verdict = final
    run.breaker_tripped = tripped
    run.reasons = reasons
    run.summary = {**run.summary, "status": final, "finished_at": finished.isoformat(),
                   "duration_ms": round((finished - started).total_seconds() * 1000, 1),
                   "totals": result["totals"] if result else None,
                   "stratum_counts": {n: len(s["selected"]) for n, s in (result["strata"] if result else {}).items()},
                   "circuit_breaker_reason": [r["code"] for r in reasons if r.get("critical")] or None}
    db.commit()
    return run


def run_dict(run: ShadowRun, *, include_diff: bool = True) -> dict[str, Any]:
    out = {"id": run.id, "learning_session_id": run.learning_session_id, "source": run.source_key,
           "proposal_version": run.proposal_version, "current_version": run.old_version,
           "candidate_version": run.new_version, "status": run.verdict, "verdict": run.verdict,
           "breaker_tripped": run.breaker_tripped, "reasons": run.reasons, "sample_count": run.sample_count,
           "strata": run.strata, "summary": run.summary, "latency": run.latency, "thresholds": run.thresholds,
           "created_by": run.created_by, "created_at": run.created_at}
    if include_diff:
        out["diff"] = run.diff
    return out


def get_run(db: Session, run_id: str) -> ShadowRun:
    run = db.get(ShadowRun, run_id)
    if run is None:
        raise ShadowError(404, f"Shadow run '{run_id}' not found")
    return run


def list_runs(db: Session, learning_session_id: str) -> list[dict[str, Any]]:
    rows = db.execute(select(ShadowRun).where(ShadowRun.learning_session_id == learning_session_id)
                      .order_by(ShadowRun.created_at.desc(), ShadowRun.id.desc()).limit(50)).scalars().all()
    return [run_dict(r, include_diff=False) for r in rows]


def latest_for_session(db: Session, session: LearningSession) -> ShadowRun | None:
    """Newest finished run for the session's CURRENT proposal and candidate."""
    rows = db.execute(select(ShadowRun).where(ShadowRun.learning_session_id == session.id,
                                              ShadowRun.proposal_version == session.proposal_version,
                                              ShadowRun.verdict != SHADOW_RUNNING)
                      .order_by(ShadowRun.created_at.desc(), ShadowRun.id.desc())).scalars().all()
    current = candidate_sha(session.candidate)
    return next((r for r in rows if (r.summary or {}).get("candidate_sha256") == current), None)


def gate(ctx) -> Any:
    """Phase 7 guard on LEARNING_ACTIVATE / LEARNING_APPROVE(activate=true)."""
    from app.config import get_settings
    from app.governance.policy import GuardDecision

    mode = get_settings().phase8_shadow_gate
    if mode == "off" or (ctx.action == "LEARNING_APPROVE" and not ctx.body.get("activate")):
        return None
    session = ctx.db.get(LearningSession, ctx.object_id) if ctx.object_id else None
    if session is None or session.status == "ACTIVE":
        return None
    run = latest_for_session(ctx.db, session)
    if run is None:
        if mode == "if_present":
            return None
        return GuardDecision(allow=False, reason=f"No shadow validation for proposal v{session.proposal_version}; "
                                                 "run POST /api/v1/shadow/runs first.",
                             evidence={"mode": mode, "proposal_version": session.proposal_version})
    evidence = {"shadow_run_id": run.id, "verdict": run.verdict, "mode": mode, "sample_count": run.sample_count,
                "reasons": [r.get("code") for r in run.reasons or []], "proposal_version": run.proposal_version}
    if run.verdict == SHADOW_BLOCKED:
        return GuardDecision(allow=False, reason=f"Shadow run {run.id} BLOCKED the candidate "
                                                 f"({', '.join(evidence['reasons'])}).", evidence=evidence)
    if run.verdict == SHADOW_REVIEW_REQUIRED:
        return GuardDecision(elevated=True, reason=f"Shadow run {run.id} requires review "
                                                   f"({', '.join(evidence['reasons'])}).", evidence=evidence)
    return GuardDecision(reason=f"Shadow run {run.id} PASSED.", evidence=evidence)
