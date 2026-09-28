"""Phase 8 Step 4: statistical drift -> semantic advisories, per source.

    Structural (Phase 5, unchanged)  ->  Statistical  ->  Semantic (advisory)

Reads stored events only; never touches Phase 5 baselines, event status or
the Phase 5 drift API. Work is bounded and deterministic:

- windows: current = [end - current_hours, end), baseline = the
  `baseline_hours` before it; `end` defaults to the start of the current UTC
  hour, so repeated runs within an hour analyze the same windows;
- per window at most MAX_EVENTS_PER_WINDOW events (newest first, ties by
  event_id); at most MAX_SOURCES sources per run;
- both windows need >= MIN_SAMPLE events, else the source is reported as
  skipped with the counts (no finding is invented);
- a finding's id is sha256(layer|source|field|metric|window_end), so a rerun
  over the same windows updates the same finding instead of adding one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.phase8 import LAYER_SEMANTIC, LAYER_STATISTICAL, DriftFinding
from app.phase8 import drift_semantic as sem
from app.phase8 import drift_stats as ds

DEFAULT_BASELINE_HOURS = 168
DEFAULT_CURRENT_HOURS = 1
MAX_EVENTS_PER_WINDOW = 20000
MAX_SOURCES = 50
MAX_EXTENSION_FIELDS = 10
STRUCTURAL_DRIFT_STATUSES = ("DRIFT", "POSSIBLE_FORMAT_DRIFT")

_SIGNATURE = Event.structural_fingerprint["signature"].astext
_DRIFT_STATUS = Event.processing_metadata["drift"]["status"].astext


class FindingNotFound(Exception):
    pass


class AnalysisError(ValueError):
    pass


def windows(end: datetime | None, baseline_hours: int, current_hours: int) -> dict[str, datetime]:
    if end is None:
        end = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    elif end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    current_start = end - timedelta(hours=current_hours)
    return {"baseline_start": current_start - timedelta(hours=baseline_hours), "baseline_end": current_start,
            "current_start": current_start, "current_end": end}


def _value(row: dict[str, Any], name: str) -> str | None:
    if name.startswith(ds.EXTENSION_PREFIX):
        return ds.normalize_value((row["extensions"] or {}).get(name[len(ds.EXTENSION_PREFIX):]))
    group, _, attr = name.partition(".")
    if attr:
        return ds.normalize_value((row[group] or {}).get(attr))
    return ds.normalize_value(row[name])


def _rows(db: Session, source: str, start: datetime, end: datetime, *, extensions: bool) -> tuple[list[dict], bool]:
    cols = [Event.event_id, Event.event_action, Event.severity, Event.network, Event.user,
            _SIGNATURE.label("signature"), _DRIFT_STATUS.label("drift_status")]
    if extensions:
        cols.append(Event.extensions)
    rows = db.execute(
        select(*cols).where(Event.adapter_id == source, Event.received_at >= start, Event.received_at < end,
                            Event.status != "FAILED")
        .order_by(Event.received_at.desc(), Event.event_id.desc()).limit(MAX_EVENTS_PER_WINDOW + 1)
    ).mappings().all()
    truncated = len(rows) > MAX_EVENTS_PER_WINDOW
    out = []
    for r in rows[:MAX_EVENTS_PER_WINDOW]:
        d = dict(r)
        d.setdefault("extensions", None)
        out.append(d)
    return out, truncated


def _finding_id(layer: str, source: str, field: str, metric: str, end: datetime) -> str:
    return hashlib.sha256(f"{layer}|{source}|{field}|{metric}|{end.isoformat()}".encode()).hexdigest()


def _persist(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    """Upsert; a rerun refreshes the numbers but keeps the review state, which is returned."""
    stmt = pg_insert(DriftFinding).values(**values, status="OPEN")
    refresh = {k: stmt.excluded[k] for k in ("baseline_value", "current_value", "deviation", "threshold", "baseline_n",
                                             "current_n", "quality", "explanation", "parent_finding_id")}
    stmt = stmt.on_conflict_do_update(index_elements=[DriftFinding.id], set_=refresh)
    status, by = db.execute(stmt.returning(DriftFinding.status, DriftFinding.acknowledged_by)).one()
    return {**values, "status": status, "acknowledged_by": by}


def candidate_sources(db: Session, w: dict[str, datetime]) -> list[str]:
    return list(db.execute(
        select(Event.adapter_id).where(Event.adapter_id.is_not(None), Event.received_at >= w["current_start"],
                                       Event.received_at < w["current_end"])
        .group_by(Event.adapter_id).order_by(Event.adapter_id).limit(MAX_SOURCES + 1)
    ).scalars().all())


def analyze(db: Session, *, source_key: str | None = None, window_end: datetime | None = None,
            baseline_hours: int = DEFAULT_BASELINE_HOURS, current_hours: int = DEFAULT_CURRENT_HOURS,
            extension_fields: list[str] | None = None, persist: bool = True) -> dict[str, Any]:
    ext = [f if f.startswith(ds.EXTENSION_PREFIX) else ds.EXTENSION_PREFIX + f for f in (extension_fields or [])]
    ext = list(dict.fromkeys(ext))
    if len(ext) > MAX_EXTENSION_FIELDS:
        raise AnalysisError(f"At most {MAX_EXTENSION_FIELDS} opt-in extension fields per run.")
    if any(len(f) > 128 or f == ds.EXTENSION_PREFIX for f in ext):
        raise AnalysisError("Extension field names must be non-empty and at most 128 characters.")
    w = windows(window_end, baseline_hours, current_hours)
    sources = [source_key] if source_key else candidate_sources(db, w)
    truncated_sources = len(sources) > MAX_SOURCES
    reports = [_analyze_source(db, s, w, ext, persist) for s in sources[:MAX_SOURCES]]
    if persist:
        db.commit()
    return {
        "window": {k: v.isoformat() for k, v in w.items()},
        "config": {"baseline_hours": baseline_hours, "current_hours": current_hours, "min_sample": ds.MIN_SAMPLE,
                   "top_k": ds.TOP_K, "max_events_per_window": MAX_EVENTS_PER_WINDOW, "max_sources": MAX_SOURCES,
                   "fields": list(ds.MONITORED_FIELDS) + ext,
                   "thresholds": {m: {"threshold": t, "high": h} for m, (t, h) in ds.THRESHOLDS.items()}},
        "sources_analyzed": len(reports),
        "sources_truncated": truncated_sources,
        "findings": sum(len(r.get("findings", [])) for r in reports),
        "advisories": sum(len(r.get("advisories", [])) for r in reports),
        "sources": reports,
        "layers": ["STRUCTURAL (Phase 5, unchanged)", "STATISTICAL", "SEMANTIC (advisory)"],
    }


def _analyze_source(db: Session, source: str, w: dict[str, datetime], ext: list[str], persist: bool) -> dict[str, Any]:
    base, base_trunc = _rows(db, source, w["baseline_start"], w["baseline_end"], extensions=bool(ext))
    cur, cur_trunc = _rows(db, source, w["current_start"], w["current_end"], extensions=bool(ext))
    report: dict[str, Any] = {"source_key": source, "baseline_n": len(base), "current_n": len(cur),
                              "baseline_truncated": base_trunc, "current_truncated": cur_trunc}
    if len(base) < ds.MIN_SAMPLE or len(cur) < ds.MIN_SAMPLE:
        report.update(status="SKIPPED_INSUFFICIENT_SAMPLES",
                      reason=f"Needs >= {ds.MIN_SAMPLE} events in each window (baseline {len(base)}, current {len(cur)}).")
        return report
    fields = list(ds.MONITORED_FIELDS) + ext
    base_vals = {f: [_value(r, f) for r in base] for f in fields}
    cur_vals = {f: [_value(r, f) for r in cur] for f in fields}
    quality = ds.quality_for(len(base), len(cur))
    evaluated: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    finding_ids: dict[tuple[str, str], str] = {}
    for f in fields:
        numeric = ds.is_numeric_field(f, base_vals[f] + cur_vals[f])
        for sig in ds.compare_field(f, base_vals[f], cur_vals[f], numeric=numeric):
            evaluated.append({"field": f, "metric": sig.metric, "deviation": sig.deviation, "threshold": sig.threshold,
                              "severity": sig.severity})
            if not sig.is_finding:
                continue
            fid = _finding_id(LAYER_STATISTICAL, source, f, sig.metric, w["current_end"])
            finding_ids[(f, sig.metric)] = fid
            row = {"id": fid, "layer": LAYER_STATISTICAL, "source_key": source, "field": f, "metric": sig.metric,
                   **w, "baseline_value": sig.baseline_value, "current_value": sig.current_value,
                   "deviation": sig.deviation, "threshold": sig.threshold, "baseline_n": len(base),
                   "current_n": len(cur), "quality": quality, "advisory": False, "parent_finding_id": None,
                   "explanation": sig.explanation + f" Evidence: {_counts_text(sig.evidence)}."}
            if persist:
                row = _persist(db, row)
            findings.append(finding_dict_from_values(row, sig.evidence))
    report.update(status="ANALYZED", quality=quality, evaluated=evaluated, findings=findings,
                  advisories=_semantic(db, source, w, base, cur, base_vals, cur_vals, finding_ids, quality, persist))
    return report


def _counts_text(evidence: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in evidence.items() if isinstance(v, int) and not isinstance(v, bool))


def _semantic(db: Session, source: str, w: dict[str, datetime], base: list[dict], cur: list[dict],
              base_vals: dict[str, list], cur_vals: dict[str, list], finding_ids: dict[tuple[str, str], str],
              quality: str, persist: bool) -> list[dict[str, Any]]:
    """Runs only on top of statistical evidence; output is always advisory."""
    flagged = {f for f, _ in finding_ids}
    if not ({"event_action", "severity"} & flagged):
        return []
    structure = sem.structure_stable([r["signature"] for r in base], [r["signature"] for r in cur],
                                     sum(1 for r in cur if r["drift_status"] in STRUCTURAL_DRIFT_STATUSES))
    ctx = ("event_action",) + sem.CONTEXT_FIELDS
    base_rows = [{f: base_vals[f][i] for f in ctx} for i in range(len(base))]
    cur_rows = [{f: cur_vals[f][i] for f in ctx} for i in range(len(cur))]
    out: list[dict[str, Any]] = []
    candidates = []
    if "event_action" in flagged:
        candidates.append((sem.action_relabel(base_rows, cur_rows, stable_fields=set(sem.CONTEXT_FIELDS) - flagged,
                                              structure=structure), "event_action"))
    if "severity" in flagged:
        candidates.append((sem.severity_remap(base_rows, cur_rows, action_stable="event_action" not in flagged,
                                              structure=structure), "severity"))
    for adv, parent_field in candidates:
        if adv is None:
            continue
        parent = next(fid for (f, _), fid in sorted(finding_ids.items()) if f == parent_field)
        row = {"id": _finding_id(LAYER_SEMANTIC, source, adv.field, adv.metric, w["current_end"]), "layer": LAYER_SEMANTIC,
               "source_key": source, "field": adv.field, "metric": adv.metric, **w,
               "baseline_value": adv.baseline_value, "current_value": adv.current_value, "deviation": adv.deviation,
               "threshold": adv.threshold, "baseline_n": len(base), "current_n": len(cur), "quality": quality,
               "advisory": True, "parent_finding_id": parent, "explanation": adv.explanation}
        if persist:
            row = _persist(db, row)
        out.append(finding_dict_from_values(row, adv.evidence))
    return out


# --------------------------------------------------------------------------
# Serialization / queries
# --------------------------------------------------------------------------


def _severity(layer: str, metric: str, deviation: float) -> str:
    if layer == LAYER_SEMANTIC:
        return "ADVISORY"
    return ds.severity_for(metric, deviation) or "INFO"


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def finding_dict_from_values(v: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    layer = v["layer"]
    out = {
        "id": v["id"], "layer": layer, "source": v["source_key"], "field": v["field"], "metric": v["metric"],
        "baseline_value": v["baseline_value"], "current_value": v["current_value"], "deviation": v["deviation"],
        "threshold": v["threshold"], "severity": _severity(layer, v["metric"], v["deviation"]),
        "deterministic_reason": (ds.reason_for(v["metric"]) if layer == LAYER_STATISTICAL
                                 else f"{v['metric']}_HEURISTIC_MATCHED"),
        "explanation": v["explanation"], "advisory": v["advisory"], "quality": v["quality"],
        "evidence_counts": {"baseline_events": v["baseline_n"], "current_events": v["current_n"]},
        "analysis_window": {k: _iso(v[k]) for k in ("baseline_start", "baseline_end", "current_start", "current_end")},
        "parent_finding_id": v.get("parent_finding_id"),
        "status": v.get("status", "OPEN"), "acknowledged_by": v.get("acknowledged_by"),
    }
    if evidence is not None:
        out["evidence"] = evidence
    if v.get("created_at") is not None:
        out["created_at"] = _iso(v["created_at"])
    return out


def finding_dict(f: DriftFinding) -> dict[str, Any]:
    return finding_dict_from_values({c: getattr(f, c) for c in (
        "id", "layer", "source_key", "field", "metric", "baseline_start", "baseline_end", "current_start",
        "current_end", "baseline_value", "current_value", "deviation", "threshold", "baseline_n", "current_n",
        "quality", "advisory", "parent_finding_id", "explanation", "status", "acknowledged_by", "created_at")})


def list_findings(db: Session, *, layer: str | None = None, source_key: str | None = None, status: str | None = None,
                  limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(DriftFinding)
    if layer:
        stmt = stmt.where(DriftFinding.layer == layer)
    if source_key:
        stmt = stmt.where(DriftFinding.source_key == source_key)
    if status:
        stmt = stmt.where(DriftFinding.status == status)
    rows = db.execute(stmt.order_by(DriftFinding.current_end.desc(), DriftFinding.source_key, DriftFinding.field,
                                    DriftFinding.metric, DriftFinding.layer).limit(limit)).scalars().all()
    return [finding_dict(r) for r in rows]


def get_finding(db: Session, finding_id: str) -> dict[str, Any]:
    row = db.get(DriftFinding, finding_id)
    if row is None:
        raise FindingNotFound(f"Finding '{finding_id}' not found")
    out = finding_dict(row)
    out["children"] = [finding_dict(c) for c in db.execute(
        select(DriftFinding).where(DriftFinding.parent_finding_id == finding_id).order_by(DriftFinding.id)).scalars()]
    return out


def acknowledge(db: Session, finding_id: str, *, by: str) -> DriftFinding:
    row = db.get(DriftFinding, finding_id)
    if row is None:
        raise FindingNotFound(f"Finding '{finding_id}' not found")
    row.status = "ACKNOWLEDGED"
    row.acknowledged_by = by[:128]
    return row


def stats(db: Session) -> dict[str, Any]:
    rows = db.execute(select(DriftFinding.layer, DriftFinding.status, func.count())
                      .group_by(DriftFinding.layer, DriftFinding.status)).all()
    return {"by_layer_status": {f"{layer}:{status}": n for layer, status, n in rows},
            "total": sum(n for *_, n in rows)}


def profile_source(db: Session, source: str, *, window_end: datetime | None = None,
                   hours: int = DEFAULT_BASELINE_HOURS) -> dict[str, Any]:
    """Bounded statistical profile of a source over one window (golden pinning)."""
    end = windows(window_end, 0, 0)["current_end"]
    start = end - timedelta(hours=hours)
    rows, truncated = _rows(db, source, start, end, extensions=False)
    fields = {f: ds.profile([_value(r, f) for r in rows], numeric=f in ds.NUMERIC_FIELDS) for f in ds.MONITORED_FIELDS}
    return {"window": {"start": start.isoformat(), "end": end.isoformat()}, "n": len(rows), "truncated": truncated,
            "sufficient": len(rows) >= ds.MIN_SAMPLE, "min_sample": ds.MIN_SAMPLE, "fields": fields}
