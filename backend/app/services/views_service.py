"""Read-only operational intelligence: event queries, summaries, event
lineage, source intelligence and adapter-evolution timelines.

Everything is derived from stored data (events, adapter versions, baselines,
onboarding and learning sessions). Nothing is parsed, normalized, learned,
activated or written: the only computation beyond reads is recomputing a
stored raw event's SHA-256 to verify its integrity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.loader import get_adapter_registry
from app.db.models.event import Event
from app.db.models.learning import LearningSession
from app.db.models.onboarding import OnboardedAdapter, OnboardingSession
from app.db.repository import views_repo as repo
from app.learning.delta import current_mappings
from app.pipeline.hashing import sha256_hex
from app.schema.adapter import AdapterMapping

OK, WARN, FAIL, SKIPPED = "OK", "WARN", "FAIL", "SKIPPED"


class ViewsNotFound(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def event_row(e: Event) -> dict[str, Any]:
    pm = e.processing_metadata or {}
    drift = pm.get("drift") if isinstance(pm.get("drift"), dict) else {}
    return {
        "event_id": e.event_id, "received_at": e.received_at, "event_timestamp": e.event_timestamp,
        "status": e.status, "format_detected": e.format_detected, "vendor": e.vendor, "product": e.product,
        "source_key": drift.get("source_key") or e.adapter_id, "adapter_id": e.adapter_id,
        "adapter_version": e.adapter_version, "adapter_source": pm.get("adapter_source"),
        "ocsf_class_name": e.ocsf_class_name, "ocsf_category_name": e.ocsf_category_name,
        "event_type": e.event_type, "event_action": e.event_action, "severity": e.severity,
        "drift_status": drift.get("status"), "drift_severity": drift.get("severity"),
        "warning_count": len(e.warnings or []), "preserved_field_count": len(e.extensions or {}),
        "raw_hash": e.raw_hash,
    }


def summary(db: Session, f: repo.EventFilters, bucket: str) -> dict[str, Any]:
    by_status = repo.grouped_counts(db, f, Event.status)
    by_drift = repo.grouped_counts(db, f, repo.DRIFT_STATUS)
    registry = get_adapter_registry().all()
    return {
        "window": {"start": f.start, "end": f.end, "bucket": bucket},
        "totals": {
            "events": sum(by_status.values()),
            "unique_sources": repo.distinct_count(db, f, Event.adapter_id),
            "unique_vendors": repo.distinct_count(db, f, Event.vendor),
            "pending_reviews": by_status.get("UNDER_REVIEW", 0),
            "drift_events": by_drift.get("DRIFT", 0) + by_drift.get("POSSIBLE_FORMAT_DRIFT", 0),
            "baselines": repo.baselines_count(db),
        },
        "by_status": by_status,
        "by_format": repo.grouped_counts(db, f, Event.format_detected),
        "by_vendor": repo.grouped_counts(db, f, Event.vendor, limit=20),
        "by_adapter": repo.grouped_counts(db, f, Event.adapter_id, limit=50),
        "by_drift_status": by_drift,
        "by_drift_severity": {k: v for k, v in repo.grouped_counts(db, f, repo.DRIFT_SEVERITY).items() if k != "NONE"},
        "adapters": {
            "shipped_vendor": sum(1 for a in registry if a.match is not None),
            "shipped_generic": sum(1 for a in registry if a.match is None),
            "onboarded_active": repo.active_onboarded_count(db),
        },
        "onboarding_sessions": repo.counts_by(db, OnboardingSession, OnboardingSession.status),
        "learning_sessions": repo.counts_by(db, LearningSession, LearningSession.status),
        "trend": repo.trend(db, f, bucket),
    }


def filter_values(db: Session) -> dict[str, Any]:
    return {
        "statuses": repo.distinct_values(db, Event.status),
        "formats": repo.distinct_values(db, Event.format_detected),
        "vendors": repo.distinct_values(db, Event.vendor),
        "products": repo.distinct_values(db, Event.product),
        "sources": repo.source_keys(db),
        "adapters": repo.adapter_versions(db),
        "categories": repo.distinct_values(db, Event.ocsf_category_name),
        "severities": repo.distinct_values(db, Event.severity),
        "drift_statuses": repo.distinct_values(db, repo.DRIFT_STATUS),
        "drift_severities": repo.distinct_values(db, repo.DRIFT_SEVERITY),
    }


# --------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------

_WARNING_KINDS = (
    ("is not a valid", "TYPE_MISMATCH_PRESERVED"),
    ("Could not coerce", "COERCION_FAILED"),
    ("Mapping conflict", "MAPPING_CONFLICT"),
    ("truncated", "TRUNCATED"),
    ("No PRI header", "SYSLOG_HEADER_MISSING"),
    ("No adapter mapping matched", "NO_ADAPTER"),
    ("timestamp", "TIMESTAMP"),
)


def classify_warning(message: str) -> str:
    lowered = message.lower()
    for needle, kind in _WARNING_KINDS:
        if needle.lower() in lowered:
            return kind
    return "OTHER"


def lineage(db: Session, event_id: str) -> dict[str, Any]:
    e = repo.get_event(db, event_id)
    if e is None:
        raise ViewsNotFound(f"Event '{event_id}' not found")
    pm = e.processing_metadata or {}
    drift = pm.get("drift") if isinstance(pm.get("drift"), dict) else None
    fp = e.structural_fingerprint or {}
    parsed: list[str] = list(fp.get("field_order") or [])
    extensions: dict[str, Any] = dict(e.extensions or {})
    chain: list[dict[str, Any]] = []

    def stage(name, outcome, summary_text, **details):
        chain.append({"stage": name, "outcome": outcome, "summary": summary_text, "details": details})

    # RAW + integrity
    recomputed = sha256_hex(e.raw_event)
    verified = recomputed == e.raw_hash
    raw_bytes = len(e.raw_event.encode("utf-8"))
    integrity = {"algorithm": "SHA-256", "stored": e.raw_hash, "recomputed": recomputed,
                 "verified": verified, "raw_bytes": raw_bytes}
    stage("RAW", OK if verified else FAIL,
          f"{raw_bytes} bytes preserved verbatim; SHA-256 {'verified' if verified else 'MISMATCH'}",
          received_at=e.received_at, raw_hash=e.raw_hash, integrity_verified=verified)

    # FORMAT DETECTION
    fmt = e.format_detected
    if fmt == "unknown":
        stage("FORMAT_DETECTION", FAIL, "Not a recognized format; no approved declarative adapter matched", format=fmt)
    elif fmt in ("kv", "delimited"):
        stage("FORMAT_DETECTION", OK, f"Unknown to the built-in detector; recognized as {fmt} by an approved onboarded adapter",
              format=fmt, via="approved declarative adapter")
    else:
        stage("FORMAT_DETECTION", OK, f"Detected as {fmt}", format=fmt, via="built-in detector")

    # PARSER
    parser = pm.get("parser")
    if e.status == "FAILED":
        stage("PARSER", FAIL, e.error_message or "Parsing failed", parser=parser,
              pipeline_version=pm.get("pipeline_version"), error=e.error_message)
    else:
        stage("PARSER", OK, f"{parser} parser extracted {len(parsed)} top-level field(s)", parser=parser,
              pipeline_version=pm.get("pipeline_version"), fields=parsed)

    # ADAPTER @ VERSION
    mapping, adapter_details = _adapter_at_version(db, e)
    if e.adapter_id is None:
        stage("ADAPTER", SKIPPED, "No adapter (event not parsed)")
    else:
        outcome = WARN if adapter_details.get("superseded_since") else OK
        stage("ADAPTER", outcome, adapter_details.pop("summary"), **adapter_details)

    # NORMALIZATION
    if e.status == "FAILED":
        stage("NORMALIZATION", SKIPPED, "Not normalized (event failed); raw event preserved")
    else:
        stage("NORMALIZATION", WARN if e.status == "PARTIAL" else OK,
              f"{e.status}: {e.vendor or '?'} / {e.product or '?'} → OCSF {e.ocsf_class_name or '?'}",
              vendor=e.vendor, product=e.product, ocsf_class=e.ocsf_class_name, ocsf_category=e.ocsf_category_name,
              event_type=e.event_type, event_action=e.event_action, severity=e.severity,
              event_timestamp=e.event_timestamp, status=e.status)

    # FIELD ACCOUNTING
    accounting = _field_accounting(e, parsed, extensions, mapping)
    if e.status == "FAILED":
        stage("FIELD_ACCOUNTING", SKIPPED, "No fields were parsed; the raw event is preserved verbatim")
    else:
        stage("FIELD_ACCOUNTING", FAIL if accounting["unaccounted"] else OK,
              f"{accounting['parsed_count']} parsed = {accounting['mapped_count']} mapped + "
              f"{accounting['preserved_count']} preserved in extensions"
              + (f"; {len(accounting['unaccounted'])} UNACCOUNTED" if accounting["unaccounted"] else ""),
              **{k: v for k, v in accounting.items() if k in ("parsed_count", "mapped_count", "preserved_count")})

    # WARNINGS
    warnings = [{"kind": classify_warning(w), "message": w} for w in (e.warnings or [])]
    stage("WARNINGS", WARN if warnings else OK,
          f"{len(warnings)} warning(s)" if warnings else "No warnings", warnings=warnings)

    # DRIFT DECISION
    if drift is None:
        stage("DRIFT_DECISION", SKIPPED, _no_drift_reason(e, pm))
    else:
        status = drift.get("status")
        outcome = {"NORMAL": OK, "BASELINE_CREATED": OK, "ERROR": FAIL}.get(status, WARN)
        stage("DRIFT_DECISION", outcome,
              f"{status}" + (f" (similarity {drift.get('similarity')}, threshold {drift.get('threshold')})"
                             if drift.get("similarity") is not None else ""),
              status=status, source_key=drift.get("source_key"), similarity=drift.get("similarity"),
              threshold=drift.get("threshold"), severity=drift.get("severity"), change_types=drift.get("change_types"),
              matched=drift.get("matched"), baseline_version=drift.get("baseline_version"),
              review=drift.get("review"), reonboarding_required=drift.get("reonboarding_required"))

    # BASELINE (current relation of this event's structure to the source baseline)
    key = (drift or {}).get("source_key") or e.adapter_id
    base = repo.baseline(db, key) if key else None
    if base is None:
        stage("BASELINE", SKIPPED, "No Phase 5 baseline for this source")
    else:
        relation = _baseline_relation(fp, base)
        stage("BASELINE", OK if relation != "not accepted" else WARN,
              f"Baseline v{base.version} ({base.origin}); this structure is {relation}",
              source_key=key, baseline_version=base.version, origin=base.origin, relation=relation,
              accepted_variants=len(base.accepted_variants or []))

    # LEARNING / ONBOARDING HISTORY
    learning = [
        {"learning_session_id": s.id, "status": s.status, "role": "trigger" if s.trigger_event_id == e.event_id else "evidence",
         "source_version": s.source_adapter_version, "target_version": s.target_version, "created_at": s.created_at}
        for s in repo.learning_sessions_referencing(db, e.event_id)
    ]
    onboarding = [
        {"onboarding_session_id": s.id, "status": s.status, "adapter_id": s.adapter_id, "adapter_version": s.adapter_version}
        for s in repo.onboarding_sessions_referencing(db, e.event_id)
    ]
    stage("LEARNING_HISTORY", OK if (learning or onboarding) else SKIPPED,
          f"Used by {len(onboarding)} onboarding session(s) and {len(learning)} learning session(s)"
          if (learning or onboarding) else "Not used as onboarding or learning evidence",
          learning_sessions=learning, onboarding_sessions=onboarding)

    silent = bool(accounting["unaccounted"]) or not verified
    basis = [
        f"Raw event preserved verbatim; SHA-256 recomputed and {'matches' if verified else 'DOES NOT match'} the stored hash.",
        (f"Every parsed field is either mapped ({accounting['mapped_count']}) or preserved in extensions "
         f"({accounting['preserved_count']})." if not accounting["unaccounted"]
         else f"{len(accounting['unaccounted'])} parsed field(s) are neither mapped nor preserved: {accounting['unaccounted']}.")
        if e.status != "FAILED" else "The event was not parsed; nothing was extracted, the raw event is the record.",
        f"Every lossy or partial step is recorded as a warning ({len(warnings)}).",
    ]
    return {"event_id": e.event_id, "status": e.status, "chain": chain, "integrity": integrity,
            "field_accounting": accounting, "nothing_silently_discarded": not silent, "basis": basis}


def _adapter_at_version(db: Session, e: Event) -> tuple[AdapterMapping | None, dict[str, Any]]:
    if e.adapter_id is None:
        return None, {}
    pm = e.processing_metadata or {}
    if pm.get("adapter_source") == "onboarded" and e.adapter_version and e.adapter_version.isdigit():
        row = repo.onboarded_version(db, e.adapter_id, int(e.adapter_version))
        versions = repo.onboarded_versions(db, e.adapter_id)
        active = next((v for v in versions if v.status == "ACTIVE"), None)
        if row is None:
            return None, {"summary": f"{e.adapter_id}@v{e.adapter_version} (onboarded; version record not found)",
                          "adapter_id": e.adapter_id, "version": e.adapter_version, "origin": "onboarded"}
        summary_ = row.validation_summary or {}
        origin = "phase6_learning" if summary_.get("origin") == "phase6_learning" else "phase3_onboarding"
        details = {
            "summary": f"{e.adapter_id}@v{row.version} (onboarded via {origin.replace('_', ' ')}; version now {row.status})",
            "adapter_id": e.adapter_id, "version": row.version, "origin": origin, "version_status_now": row.status,
            "session_id": row.session_id, "approved_by": row.approved_by, "approved_at": row.approved_at,
            "active_version_now": active.version if active else None,
            "superseded_since": row.status != "ACTIVE",
        }
        try:
            return AdapterMapping.model_validate(row.mapping), details
        except Exception:  # noqa: BLE001 — a stored mapping that no longer validates is reported, not fatal
            return None, details
    adapter = get_adapter_registry().get(e.adapter_id)
    kind = "shipped vendor adapter" if adapter is not None and adapter.match is not None else "shipped generic adapter"
    return adapter, {
        "summary": f"{e.adapter_id}@v{e.adapter_version} ({kind}, YAML)",
        "adapter_id": e.adapter_id, "version": e.adapter_version, "origin": "shipped_yaml", "kind": kind,
        "note": "shipped YAML adapters are not versioned in the database; accounting uses the currently loaded definition",
        "superseded_since": False,
    }


def _normalized_value(e: Event, target: str) -> Any:
    if target == "event_action":
        return e.event_action
    if target == "severity":
        return e.severity
    if target == "timestamp":
        return e.event_timestamp
    if target == "product_version":
        return e.product_version
    group, sep, attr = target.partition(".")
    if sep and group in ("network", "user", "process"):
        return (getattr(e, group) or {}).get(attr)
    return (e.normalized_event or {}).get(target)


def _field_accounting(e: Event, parsed: list[str], extensions: dict[str, Any], mapping: AdapterMapping | None) -> dict[str, Any]:
    targets = current_mappings(mapping) if mapping is not None else {}
    fields: list[dict[str, Any]] = []
    unaccounted: list[str] = []
    for name in parsed:
        if name in extensions:
            fields.append({"field": name, "outcome": "PRESERVED", "location": f"extensions.{name}",
                           "value": extensions[name]})
        elif name in targets:
            target = targets[name]["target"]
            value = _normalized_value(e, target)
            fields.append({"field": name, "outcome": "MAPPED", "target": target,
                           "normalized_value_present": value is not None})
        else:
            fields.append({"field": name, "outcome": "UNACCOUNTED"})
            unaccounted.append(name)
    stray = sorted(set(extensions) - set(parsed))
    return {
        "parsed_count": len(parsed),
        "mapped_count": sum(1 for f in fields if f["outcome"] == "MAPPED"),
        "preserved_count": sum(1 for f in fields if f["outcome"] == "PRESERVED"),
        "unaccounted": unaccounted,
        "extensions_not_in_parse": stray,
        "fields": fields,
    }


def _no_drift_reason(e: Event, pm: dict[str, Any]) -> str:
    if e.status == "FAILED":
        return "Not evaluated: FAILED events are never drift-checked"
    if e.adapter_id is None:
        return "Not evaluated: no adapter"
    adapter = get_adapter_registry().get(e.adapter_id)
    if adapter is not None and adapter.match is None:
        return "Not evaluated: generic adapter with no evidence of a known vendor source"
    return "Not evaluated (drift detection was disabled or the event predates it)"


def _same_structure(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b) and a.get("field_order") == b.get("field_order") and a.get("field_types") == b.get("field_types")


def _baseline_relation(fp: dict[str, Any], base) -> str:
    if _same_structure(fp, base.fingerprint):
        return "the reference structure"
    for i, v in enumerate(base.accepted_variants or []):
        if _same_structure(fp, v.get("fingerprint")):
            return f"approved variant {i}"
    return "not accepted"


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def list_sources(db: Session) -> list[dict[str, Any]]:
    return [_source_summary(db, key) for key in repo.source_keys(db)]


def source_detail(db: Session, key: str) -> dict[str, Any]:
    if key not in repo.source_keys(db):
        raise ViewsNotFound(f"Source '{key}' not found")
    data = _source_summary(db, key)
    data["versions"] = [
        {"version": v.version, "status": v.status, "origin": (v.validation_summary or {}).get("origin", "phase3_onboarding"),
         "session_id": v.session_id, "approved_by": v.approved_by, "approved_at": v.approved_at,
         "deactivated_at": v.deactivated_at, "match_rate": (v.validation_summary or {}).get("match_rate"),
         "mapping": v.mapping}
        for v in repo.onboarded_versions(db, key)
    ]
    data["baseline_history"] = [
        {"version": h.version, "action": h.action, "event_id": h.event_id, "signature": h.signature,
         "field_count": h.field_count, "changes": h.changes, "note": h.note, "created_at": h.created_at}
        for h in repo.baseline_history(db, key)
    ]
    data["recent_drift"] = [
        {**event_row(e), "change_types": (e.processing_metadata or {}).get("drift", {}).get("change_types"),
         "review": (e.processing_metadata or {}).get("drift", {}).get("review")}
        for e in repo.recent_drift_events(db, key)
    ]
    return data


def _source_summary(db: Session, key: str) -> dict[str, Any]:
    stats = repo.source_event_stats(db, key)
    versions = repo.onboarded_versions(db, key)
    adapter = get_adapter_registry().get(key)
    if versions:
        kind = "onboarded"
    elif adapter is not None:
        kind = "shipped_vendor" if adapter.match is not None else "shipped_generic"
    else:
        kind = "unknown"
    active = next((v for v in versions if v.status == "ACTIVE"), None)
    latest = stats["latest"]
    base = repo.baseline(db, key)
    total = sum(stats["status"].values())
    learning: dict[str, int] = {}
    for s in repo.learning_sessions_for_source(db, key):
        learning[s.status] = learning.get(s.status, 0) + 1
    active_mapping = active.mapping if active else None
    return {
        "source_key": key,
        "kind": kind,
        "vendor": latest[0] if latest else (active_mapping or {}).get("vendor") or (adapter.vendor if adapter else None),
        "product": latest[1] if latest else (active_mapping or {}).get("product") or (adapter.product if adapter else None),
        "events": {"total": total, **stats["status"]},
        "partial_rate": round(stats["status"].get("PARTIAL", 0) / total, 4) if total else None,
        "formats": stats["formats"],
        "adapter_versions_seen": stats["versions"],
        "active_version": str(active.version) if active else (adapter.version if adapter else None),
        "baseline": None if base is None else {
            "version": base.version, "origin": base.origin, "reference_field_count": (base.fingerprint or {}).get("field_count"),
            "accepted_variants": len(base.accepted_variants or []), "adapter_version": base.adapter_version,
            "updated_at": base.updated_at,
        },
        "drift": stats["drift"],
        "under_review": stats["under_review"],
        "learning_sessions": learning,
        "last_seen": latest[2] if latest else None,
    }


# --------------------------------------------------------------------------
# Adapter evolution timeline
# --------------------------------------------------------------------------


def timeline(db: Session, key: str) -> dict[str, Any]:
    if key not in repo.source_keys(db):
        raise ViewsNotFound(f"Source '{key}' not found")
    entries: list[dict[str, Any]] = []

    def add(at, phase, kind, title, details=None, refs=None):
        at = _as_datetime(at)
        if at is not None:
            entries.append({"at": at, "phase": phase, "kind": kind, "title": title,
                            "details": details or {}, "refs": refs or {}})

    for v in repo.onboarded_versions(db, key):
        origin = (v.validation_summary or {}).get("origin", "phase3_onboarding")
        # created_at = when the version row was written, i.e. when it became ACTIVE
        # (for learned versions approval and activation are separate actions).
        add(v.created_at, "adapter", "VERSION_ACTIVATED", f"Adapter v{v.version} activated ({origin.replace('_', ' ')})",
            {"approved_by": v.approved_by, "approved_at": v.approved_at, "match_rate": (v.validation_summary or {}).get("match_rate"),
             "learning_modes": (v.validation_summary or {}).get("learning_modes")},
            {"version": v.version, "session_id": v.session_id})
        if v.deactivated_at is not None:
            add(v.deactivated_at, "adapter", f"VERSION_{v.status}", f"Adapter v{v.version} {v.status.lower().replace('_', ' ')}",
                refs={"version": v.version})

    for s in repo.onboarding_sessions_for_adapter(db, key):
        add(s.created_at, "onboarding", "ONBOARDING_STARTED", f"Onboarding session with {s.sample_count} sample(s)",
            refs={"onboarding_session_id": s.id})
        for d in s.decisions or []:
            add(d.get("at"), "onboarding", f"ONBOARDING_{d.get('action')}", f"Onboarding {str(d.get('action')).lower()}",
                {k: d.get(k) for k in ("by", "note", "reason", "match_rate") if d.get(k) is not None},
                {"onboarding_session_id": s.id, "version": d.get("adapter_version")})

    for h in repo.baseline_history(db, key):
        add(h.created_at, "baseline", h.action, f"Baseline v{h.version}: {h.action.replace('_', ' ').lower()}",
            {"field_count": h.field_count, "changes": h.changes, "note": h.note}, {"event_id": h.event_id})

    for e in repo.recent_drift_events(db, key):
        d = (e.processing_metadata or {}).get("drift") or {}
        add(e.received_at, "drift", d.get("status"), f"{d.get('status')} detected (severity {d.get('severity')})",
            {"similarity": d.get("similarity"), "change_types": d.get("change_types")}, {"event_id": e.event_id})
        review = d.get("review")
        if review:
            add(review.get("reviewed_at"), "drift", "DRIFT_REVIEWED", f"Drift reviewed: {review.get('resolution')}",
                {"note": review.get("note")}, {"event_id": e.event_id})

    for s in repo.learning_sessions_for_source(db, key):
        for d in s.decisions or []:
            add(d.get("at"), "learning", f"LEARNING_{d.get('action')}", f"Learning {str(d.get('action')).lower().replace('_', ' ')}",
                {k: d.get(k) for k in ("by", "note", "version", "superseded_version") if d.get(k) is not None},
                {"learning_session_id": s.id, "trigger_event_id": s.trigger_event_id})

    entries.sort(key=lambda x: x["at"])
    return {"source_key": key, "entries": entries}


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
