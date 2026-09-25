"""Demo Mode: read-only progress status and a strictly scoped reset.

The demo itself never runs here: every state transition (ingest, onboarding,
approval, drift review, learning, activation, rollback) is made by a client
calling the public API (`app.demo.runner` or the console's Demo page). This
module only answers "how far has the demo got?" from the stored rows, and
removes demo-owned rows on request.

Ownership is proven, never assumed:
- events: raw_hash is the SHA-256 of a demo fixture (deterministic raw logs
  that all carry the demo device marker);
- onboarding sessions: named DEMO_ID *and* every sample is a demo fixture;
- adapter versions, learning sessions, baselines: only for DEMO_SOURCE_KEY,
  and only when every version of that adapter was created by a demo session.
If the demo source key is held by anything else, reset refuses (409).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.db.models.onboarding import OnboardedAdapter, OnboardingSession
from app.db.models.source_baseline import SourceBaseline, SourceBaselineHistory
from app.demo import fixtures as fx

STEP_TITLES = [
    ("unknown_source", "Unknown source"),
    ("samples_collected", "Samples collected"),
    ("suggestion", "Parser suggestion"),
    ("sandbox_v1", "Sandbox validation"),
    ("approve_v1", "Human approval"),
    ("adapter_v1_active", "Adapter v1 active"),
    ("events_normalized", "Events normalized"),
    ("drift_detected", "Drift detected"),
    ("drift_accepted", "Drift accepted"),
    ("learning_proposed", "Learning proposed"),
    ("learning_validated", "Learning validated"),
    ("approve_v2", "Human approval"),
    ("adapter_v2_active", "Adapter v2 active"),
    ("old_and_new_work", "Old + new structures work"),
    ("rollback_verified", "Rollback verified"),
]
# The client action that advances each step when it is next (None = verification only).
AUTO_ACTION = {
    "unknown_source": "ingest_probe",
    "samples_collected": "create_session",
    "suggestion": "suggest_offline",
    "adapter_v1_active": None,
    "events_normalized": "ingest_v1_events",
    "drift_detected": "ingest_drift",
    "learning_proposed": "propose_learning",
    "old_and_new_work": "ingest_checks",
    "rollback_verified": "ingest_post_rollback",
}
HUMAN_ACTION = {
    "approve_v1": "approve_onboarding",
    "drift_accepted": "accept_drift",
    "approve_v2": "approve_learning",
    "adapter_v2_active": "activate_learning",
    "rollback_verified": "rollback_learning",
}
LEARNING_APPROVED = ("APPROVED", "ACTIVE", "ROLLED_BACK")


class DemoConflict(Exception):
    """The demo namespace is held by data the demo cannot prove it owns."""


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def _demo_sessions(db: Session) -> list[OnboardingSession]:
    hashes = fx.raw_hashes()
    rows = db.scalars(select(OnboardingSession).where(OnboardingSession.name == fx.DEMO_ID)
                      .order_by(OnboardingSession.created_at)).all()
    return [s for s in rows if s.samples and all(x.get("raw_hash") in hashes for x in s.samples)]


def _adapter_owner_problem(db: Session, sessions: list[OnboardingSession]) -> str | None:
    """Why the demo source key is not demo-owned, or None if it is (or is free)."""
    versions = db.scalars(select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == fx.DEMO_SOURCE_KEY)).all()
    if not versions:
        return None
    onboarding_ids = {s.id for s in sessions}
    learning_ids = set(db.scalars(select(LearningSession.id).where(LearningSession.source_key == fx.DEMO_SOURCE_KEY)).all())
    foreign = [v.version for v in versions if v.session_id not in onboarding_ids | learning_ids]
    if foreign:
        return (f"adapter '{fx.DEMO_SOURCE_KEY}' has version(s) {foreign} that were not created by the demo; "
                "reset will not touch it")
    first = min(versions, key=lambda v: v.version)
    if first.session_id not in onboarding_ids:
        return f"adapter '{fx.DEMO_SOURCE_KEY}' v{first.version} was not onboarded by a demo session"
    return None


def _demo_events(db: Session) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.raw_hash.in_(fx.raw_hashes())).order_by(Event.received_at)).all())


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def status(db: Session) -> dict[str, Any]:
    sessions = _demo_sessions(db)
    problem = _adapter_owner_problem(db, sessions)
    session = sessions[-1] if sessions else None
    versions = [] if problem else db.scalars(
        select(OnboardedAdapter).where(OnboardedAdapter.adapter_id == fx.DEMO_SOURCE_KEY).order_by(OnboardedAdapter.version)).all()
    learning = [] if problem else db.scalars(
        select(LearningSession).where(LearningSession.source_key == fx.DEMO_SOURCE_KEY).order_by(LearningSession.created_at)).all()
    lsess = learning[-1] if learning else None

    events: dict[str, dict[str, Any]] = {}
    for e in _demo_events(db):
        f = fx.fixture_for_hash(e.raw_hash)
        drift = (e.processing_metadata or {}).get("drift") or {}
        events[f.key] = {  # latest wins (ordered by received_at)
            "event_id": e.event_id, "status": e.status, "adapter_id": e.adapter_id, "adapter_version": e.adapter_version,
            "drift_status": drift.get("status"), "drift_resolution": (drift.get("review") or {}).get("resolution"),
        }

    v_by = {v.version: v for v in versions}
    ev = events.get

    def ok(key: str, version: str | None = None, statuses: tuple[str, ...] = ("SUCCESS",)) -> bool:
        e = ev(key)
        return bool(e) and e["status"] in statuses and (version is None or e["adapter_version"] == version)

    v1_events = [f.key for f in fx.of_kind("EVENT_V1")]
    evidence = [f.key for f in fx.of_kind("EVIDENCE_V2")]
    validation = (session.validation or {}) if session else {}
    lval = ((lsess.validation or {}).get("result")) if lsess else None
    v2 = v_by.get(2)
    rolled_back = bool(v2 and v2.status == "ROLLED_BACK" and v_by.get(1) and v_by[1].status == "ACTIVE")

    done: dict[str, bool] = {
        "unknown_source": ok("unknown_probe", statuses=("FAILED",)),
        "samples_collected": session is not None,
        "suggestion": bool(session and session.proposal_source),
        "sandbox_v1": validation.get("result") == "PASSED",
        "approve_v1": bool(session and session.status == "APPROVED"),
        "adapter_v1_active": 1 in v_by,
        "events_normalized": all(ok(k, "1") for k in v1_events) and ok("malformed", statuses=("FAILED",)),
        "drift_detected": bool(ev("drift_trigger") and ev("drift_trigger")["drift_status"] == "DRIFT"),
        "drift_accepted": bool(ev("drift_trigger") and ev("drift_trigger")["drift_resolution"] in ("accepted_variant", "replaced_baseline")),
        "learning_proposed": lsess is not None,
        "learning_validated": lval == "PASSED",
        "approve_v2": bool(lsess and lsess.status in LEARNING_APPROVED),
        "adapter_v2_active": bool(v2 and v2.status in ("ACTIVE", "SUPERSEDED", "ROLLED_BACK") and lsess and lsess.status in ("ACTIVE", "ROLLED_BACK")),
        "old_and_new_work": ok("check_v2", "2") and ok("check_v1", "2"),
        "rollback_verified": rolled_back and ok("post_rollback_v1", "1"),
    }
    failed: dict[str, str] = {}
    if session and session.status == "SUGGESTION_FAILED":
        failed["suggestion"] = (session.suggestion_error or {}).get("message", "suggestion failed")
    if session and validation.get("result") in ("REJECTED", "NEEDS_REVIEW"):
        failed["sandbox_v1"] = "; ".join(validation.get("reasons") or []) or validation.get("result")
    if lsess and lval in ("REJECTED", "NEEDS_REVIEW") and lsess.status not in LEARNING_APPROVED:
        failed["learning_validated"] = "; ".join((lsess.validation or {}).get("reasons") or []) or lval
    if lsess and lsess.status in ("REJECTED", "FAILED", "NO_CHANGE_REQUIRED"):
        failed["learning_proposed"] = f"learning session is {lsess.status}"
    for key in v1_events + ["check_v1", "check_v2", "post_rollback_v1"] + evidence:
        e = ev(key)
        if e and e["status"] not in ("SUCCESS",):
            failed.setdefault("events_normalized" if key in v1_events else
                              "learning_proposed" if key in evidence else
                              "rollback_verified" if key == "post_rollback_v1" else "old_and_new_work",
                              f"{key} is {e['status']}")

    steps = []
    next_step = None
    for key, title in STEP_TITLES:
        if done[key]:
            state = "done"
        elif key in failed:
            state = "failed"
        elif next_step is None:
            # Rollback is a human decision first, then an automatic post-check.
            human = key in HUMAN_ACTION and not (key == "rollback_verified" and rolled_back)
            if key == "adapter_v2_active" and not (lsess and lsess.status == "APPROVED"):
                human = False
            state = "human" if human else "pending"
        else:
            state = "waiting"
        if state in ("human", "pending", "failed") and next_step is None:
            action = HUMAN_ACTION.get(key) if state == "human" else AUTO_ACTION.get(key) if state == "pending" else None
            next_step = {"step": key, "kind": state, "action": action}
        steps.append({"key": key, "title": title, "state": state, "detail": failed.get(key)})

    return {
        "namespace": {"session_name": fx.DEMO_ID, "source_key": fx.DEMO_SOURCE_KEY, "marker": fx.DEMO_MARKER},
        "exists": bool(sessions or versions or learning or events),
        "conflict": problem,
        "complete": all(done.values()),
        "next": next_step or {"step": None, "kind": "complete", "action": None},
        "steps": steps,
        "onboarding_session": None if session is None else {
            "id": session.id, "status": session.status, "proposal_version": session.proposal_version, "sample_count": session.sample_count,
            "proposal_source": session.proposal_source, "validation_result": validation.get("result"),
            "match_rate": (validation.get("metrics") or {}).get("match_rate"),
        },
        "adapter_versions": [{"version": v.version, "status": v.status, "session_id": v.session_id} for v in versions],
        "learning_session": None if lsess is None else {
            "id": lsess.id, "status": lsess.status, "proposal_version": lsess.proposal_version, "risk": lsess.risk,
            "validation_result": lval, "target_version": lsess.target_version,
            "compatibility_confirmation_required": bool((lsess.validation or {}).get("compatibility_confirmation_required")),
        },
        "events": events,
    }


# --------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------


def _foreign_counts(db: Session, session_ids: list[str]) -> dict[str, int]:
    hashes = fx.raw_hashes()
    return {
        "events": db.scalar(select(func.count()).select_from(Event).where(Event.raw_hash.not_in(hashes))),
        "onboarding_sessions": db.scalar(select(func.count()).select_from(OnboardingSession).where(OnboardingSession.id.not_in(session_ids or [""]))),
        "adapter_versions": db.scalar(select(func.count()).select_from(OnboardedAdapter).where(OnboardedAdapter.adapter_id != fx.DEMO_SOURCE_KEY)),
        "learning_sessions": db.scalar(select(func.count()).select_from(LearningSession).where(LearningSession.source_key != fx.DEMO_SOURCE_KEY)),
        "baselines": db.scalar(select(func.count()).select_from(SourceBaseline).where(SourceBaseline.source_key != fx.DEMO_SOURCE_KEY)),
        "baseline_history": db.scalar(select(func.count()).select_from(SourceBaselineHistory).where(SourceBaselineHistory.source_key != fx.DEMO_SOURCE_KEY)),
    }


def reset(db: Session) -> dict[str, Any]:
    sessions = _demo_sessions(db)
    problem = _adapter_owner_problem(db, sessions)
    if problem:
        raise DemoConflict(problem)
    session_ids = [s.id for s in sessions]
    before = _foreign_counts(db, session_ids)
    key = fx.DEMO_SOURCE_KEY
    deleted = {
        # learning sessions reference onboarded_adapters (FK) -> delete them first
        "learning_sessions": db.execute(delete(LearningSession).where(LearningSession.source_key == key)).rowcount,
        "events": db.execute(delete(Event).where(Event.raw_hash.in_(fx.raw_hashes()))).rowcount,
        "adapter_versions": db.execute(delete(OnboardedAdapter).where(OnboardedAdapter.adapter_id == key)).rowcount,
        "onboarding_sessions": db.execute(delete(OnboardingSession).where(OnboardingSession.id.in_(session_ids or [""]))).rowcount,
        "baselines": db.execute(delete(SourceBaseline).where(SourceBaseline.source_key == key)).rowcount,
        "baseline_history": db.execute(delete(SourceBaselineHistory).where(SourceBaselineHistory.source_key == key)).rowcount,
    }
    after = _foreign_counts(db, session_ids)
    if after != before:  # impossible by construction; refuse rather than commit a wider delete
        db.rollback()
        raise DemoConflict(f"reset would have changed non-demo rows ({before} -> {after}); rolled back")
    db.commit()
    return {"deleted": deleted, "non_demo_rows": {"before": before, "after": after, "unchanged": True}}
