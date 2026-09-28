"""Phase 8 Step 8: cross-vendor drift correlation (investigation aid only).

Within one window (default 60 minutes) it gathers, per source:

- Phase 5 structural drift: events whose drift status is DRIFT or
  POSSIBLE_FORMAT_DRIFT, received in the window (fields = added / removed /
  type-changed raw fields, resolved to the normalized target they map to
  through the source's adapter; unmapped fields count as `extensions`);
- Phase 8 statistical findings and semantic advisories whose current window
  overlaps it (fields = the monitored normalized field, `extensions.*`
  collapsed to `extensions`).

Sources are linked when they share a normalized target field or a change
type. A connected group is a *cross-vendor* correlation only if it spans at
least two distinct vendors (two sources of one vendor never qualify).

Score = sum of four named components, each in [0, 1] and weighted:

    source_diversity  0.30  min(1, (vendors - 1) / 3)
    change_overlap    0.25  mean Jaccard of change-type sets, over cross-vendor source pairs
    shared_fields     0.30  mean Jaccard of target-field sets, over cross-vendor source pairs
    time_proximity    0.15  1 - (spread of first-seen times / window length)

Strength: HIGH >= 0.65, MEDIUM >= 0.40, else LOW — a description of how
strongly the evidence lines up, not a severity decision. No embeddings, no
LLM. The analysis reads events / findings and writes only
drift_correlations; it never touches adapters, drift reviews, learning,
golden baselines, rollbacks or events.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.event import Event
from app.db.models.phase8 import DriftCorrelation, DriftFinding
from app.learning.delta import current_mappings
from app.phase8 import drift_stats as ds
from app.services import onboarding_service

DEFAULT_WINDOW_MINUTES = 60
MIN_VENDORS = 2
MAX_STRUCTURAL_EVENTS = 5000
MAX_FINDINGS = 2000
MAX_REFS_PER_SOURCE = 20
WEIGHTS = {"source_diversity": 0.30, "change_overlap": 0.25, "shared_fields": 0.30, "time_proximity": 0.15}
STRENGTH = (("HIGH", 0.65), ("MEDIUM", 0.40))
_IGNORED_CHANGE_TYPES = {"MULTIPLE_STRUCTURAL_CHANGE"}
_DRIFT = Event.processing_metadata["drift"]
_DRIFT_STATUS = _DRIFT["status"].astext


class CorrelationNotFound(Exception):
    pass


def window(end: datetime | None, minutes: int) -> tuple[datetime, datetime]:
    if end is None:
        end = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
    elif end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end - timedelta(minutes=minutes), end


def _target_fields(raw_fields: list[str], mapping: dict[str, dict[str, Any]]) -> set[str]:
    out = set()
    for raw in raw_fields:
        if raw in mapping:
            out.add(str(mapping[raw]["target"]))
        elif raw in ds.MONITORED_FIELDS:
            out.add(raw)
        else:
            out.add("extensions")
    return out


def _finding_field(field: str) -> str:
    return "extensions" if field.startswith(ds.EXTENSION_PREFIX) else field


def gather(db: Session, start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    registry = onboarding_service.runtime_registry(db)
    sources: dict[str, dict[str, Any]] = {}

    def entry(source: str) -> dict[str, Any]:
        return sources.setdefault(source, {"fields": set(), "change_types": set(), "kinds": set(), "refs": [],
                                           "first_seen": None, "vendors": Counter()})

    rows = db.execute(
        select(Event.event_id, Event.received_at, Event.vendor, Event.processing_metadata["drift"].label("drift"))
        .where(_DRIFT_STATUS.in_(("DRIFT", "POSSIBLE_FORMAT_DRIFT")), Event.received_at >= start, Event.received_at < end)
        .order_by(Event.received_at, Event.event_id).limit(MAX_STRUCTURAL_EVENTS)
    ).all()
    mappings: dict[str, dict[str, dict[str, Any]]] = {}
    for event_id, received_at, vendor, drift in rows:
        source = drift.get("source_key")
        if not source:
            continue
        if source not in mappings:
            adapter = registry.get(source)
            mappings[source] = current_mappings(adapter) if adapter is not None else {}
        diff = drift.get("differences") or {}
        raw_fields = list(diff.get("added_fields") or []) + list(diff.get("removed_fields") or []) \
            + list((diff.get("type_changes") or {}).keys())
        e = entry(source)
        e["fields"] |= _target_fields(raw_fields, mappings[source])
        e["change_types"] |= set(drift.get("change_types") or []) - _IGNORED_CHANGE_TYPES
        e["kinds"].add("STRUCTURAL" if drift.get("status") == "DRIFT" else "POSSIBLE_FORMAT_DRIFT")
        e["vendors"][vendor or source] += 1
        e["first_seen"] = min(filter(None, [e["first_seen"], received_at]))
        if sum(1 for r in e["refs"] if r["type"] == "event") < MAX_REFS_PER_SOURCE:
            e["refs"].append({"type": "event", "id": event_id, "kind": drift.get("status")})

    findings = db.execute(
        select(DriftFinding).where(DriftFinding.current_start < end, DriftFinding.current_end > start)
        .order_by(DriftFinding.source_key, DriftFinding.id).limit(MAX_FINDINGS)).scalars().all()
    for f in findings:
        e = entry(f.source_key)
        e["fields"].add(_finding_field(f.field))
        e["change_types"].add(f.metric)
        e["kinds"].add(f.layer)
        seen = max(f.current_start, start)
        e["first_seen"] = min(filter(None, [e["first_seen"], seen]))
        e["refs"].append({"type": "drift_finding", "id": f.id, "kind": f"{f.layer}:{f.metric}", "field": f.field})
        if not e["vendors"]:
            vendor = db.execute(select(Event.vendor).where(Event.adapter_id == f.source_key, Event.vendor.is_not(None))
                                .order_by(Event.received_at.desc()).limit(1)).scalar()
            adapter = registry.get(f.source_key)
            e["vendors"][vendor or (adapter.vendor if adapter is not None else None) or f.source_key] += 1
    for e in sources.values():
        e["vendor"] = sorted(e["vendors"], key=lambda v: (-e["vendors"][v], v))[0]
    return sources


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _components(sources: dict[str, dict[str, Any]], names: list[str], span: float) -> dict[str, Any]:
    vendors = sorted({sources[n]["vendor"] for n in names})
    pairs = [(a, b) for a, b in combinations(names, 2) if sources[a]["vendor"] != sources[b]["vendor"]]
    overlap = sum(_jaccard(sources[a]["change_types"], sources[b]["change_types"]) for a, b in pairs) / len(pairs)
    shared = sum(_jaccard(sources[a]["fields"], sources[b]["fields"]) for a, b in pairs) / len(pairs)
    times = [sources[n]["first_seen"] for n in names]
    spread = (max(times) - min(times)).total_seconds()
    values = {"source_diversity": min(1.0, (len(vendors) - 1) / 3), "change_overlap": overlap, "shared_fields": shared,
              "time_proximity": max(0.0, 1 - spread / span) if span else 1.0}
    comps = {k: {"value": round(v, 4), "weight": WEIGHTS[k], "contribution": round(WEIGHTS[k] * v, 4)}
             for k, v in values.items()}
    total = round(sum(c["contribution"] for c in comps.values()), 4)
    return {"components": comps, "total": total, "vendor_pairs": len(pairs), "first_seen_spread_seconds": spread}


def _groups(sources: dict[str, dict[str, Any]]) -> list[list[str]]:
    names = sorted(sources)
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in combinations(names, 2):
        sa, sb = sources[a], sources[b]
        if sa["fields"] & sb["fields"] or sa["change_types"] & sb["change_types"]:
            parent[find(b)] = find(a)
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return [g for g in groups.values() if len({sources[n]["vendor"] for n in g}) >= MIN_VENDORS]


def strength(total: float) -> str:
    return next((name for name, floor in STRENGTH if total >= floor), "LOW")


def analyze(db: Session, *, window_end: datetime | None = None, window_minutes: int = DEFAULT_WINDOW_MINUTES,
            persist: bool = True) -> dict[str, Any]:
    start, end = window(window_end, window_minutes)
    sources = gather(db, start, end)
    out = []
    for names in _groups(sources):
        score = _components(sources, names, (end - start).total_seconds())
        shared_fields = sorted(set.intersection(*(sources[n]["fields"] for n in names)))
        any_shared = sorted({f for a, b in combinations(names, 2) for f in sources[a]["fields"] & sources[b]["fields"]})
        refs = [{"source": n, "vendor": sources[n]["vendor"], **r} for n in names for r in sources[n]["refs"]]
        key = f"{start.isoformat()}|{end.isoformat()}|" + ",".join(names) + "|" + ",".join(sorted(r["id"] for r in refs))
        cid = hashlib.sha256(key.encode()).hexdigest()
        vendors = sorted({sources[n]["vendor"] for n in names})
        change_types = sorted(set().union(*(sources[n]["change_types"] for n in names)))
        kinds = sorted(set().union(*(sources[n]["kinds"] for n in names)))
        per_source = {n: {"vendor": sources[n]["vendor"], "fields": sorted(sources[n]["fields"]),
                          "change_types": sorted(sources[n]["change_types"]), "kinds": sorted(sources[n]["kinds"]),
                          "first_seen": sources[n]["first_seen"].isoformat(), "evidence": len(sources[n]["refs"])}
                      for n in names}
        c = score["components"]
        explanation = (
            f"{len(vendors)} vendors ({', '.join(vendors)}) across {len(names)} sources ({', '.join(names)}) drifted "
            f"between {start.isoformat()} and {end.isoformat()}. "
            f"Fields changed by more than one of them: {any_shared or 'none'}; by all: {shared_fields or 'none'}. "
            f"Change types: {change_types}. Evidence: {len(refs)} reference(s) "
            f"({', '.join(f'{n}: ' + ', '.join(sorted(per_source[n]['kinds'])) for n in names)}). "
            f"Score {score['total']} = diversity {c['source_diversity']['contribution']} + change overlap "
            f"{c['change_overlap']['contribution']} + shared fields {c['shared_fields']['contribution']} + time "
            f"proximity {c['time_proximity']['contribution']} ({strength(score['total'])}). Investigation aid only: "
            f"nothing was changed.")
        row = {"id": cid, "window_start": start, "window_end": end, "source_keys": names, "vendors": vendors,
               "drift_types": kinds, "fields": any_shared,
               "member_refs": refs, "score_components": {**score, "strength": strength(score["total"]),
                                                          "change_types": change_types, "per_source": per_source,
                                                          "fields_shared_by_all": shared_fields},
               "confidence": score["total"], "explanation": explanation}
        if persist:
            stmt = pg_insert(DriftCorrelation).values(**row, status="OPEN")
            db.execute(stmt.on_conflict_do_update(
                index_elements=[DriftCorrelation.id],
                set_={k: stmt.excluded[k] for k in ("score_components", "confidence", "explanation", "member_refs")}))
        out.append(row)
    if persist:
        db.commit()
    return {"window": {"start": start.isoformat(), "end": end.isoformat(), "minutes": window_minutes},
            "sources_with_drift": len(sources), "correlations": [_row_dict(r) for r in out],
            "rules": {"min_vendors": MIN_VENDORS, "weights": WEIGHTS, "strength": dict(STRENGTH),
                      "link": "shared normalized target field or shared change type"},
            "safety": "investigation only — no adapter, drift review, learning, golden baseline, rollback or event is changed"}


def _row_dict(r: dict[str, Any], status: str = "OPEN", created_at: Any = None) -> dict[str, Any]:
    sc = r["score_components"]
    return {"id": r["id"], "window": {"start": _iso(r["window_start"]), "end": _iso(r["window_end"])},
            "sources": r["source_keys"], "vendors": r["vendors"], "drift_types": r["drift_types"],
            "affected_fields": r["fields"], "change_types": sc.get("change_types"),
            "drift_finding_ids": [m["id"] for m in r["member_refs"] if m["type"] == "drift_finding"],
            "event_ids": [m["id"] for m in r["member_refs"] if m["type"] == "event"],
            "score": r["confidence"], "strength": sc.get("strength"), "score_breakdown": sc.get("components"),
            "per_source": sc.get("per_source"), "explanation": r["explanation"], "member_refs": r["member_refs"],
            "status": status, "created_at": created_at, "investigation_only": True}


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def to_dict(c: DriftCorrelation) -> dict[str, Any]:
    return _row_dict({k: getattr(c, k) for k in ("id", "window_start", "window_end", "source_keys", "vendors",
                                                 "drift_types", "fields", "member_refs", "score_components",
                                                 "confidence", "explanation")}, c.status, c.created_at)


def list_correlations(db: Session, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(DriftCorrelation)
    if status:
        stmt = stmt.where(DriftCorrelation.status == status)
    rows = db.execute(stmt.order_by(DriftCorrelation.window_end.desc(), DriftCorrelation.confidence.desc(),
                                    DriftCorrelation.id).limit(limit)).scalars().all()
    return [to_dict(r) for r in rows]


def get(db: Session, correlation_id: str) -> dict[str, Any]:
    row = db.get(DriftCorrelation, correlation_id)
    if row is None:
        raise CorrelationNotFound(f"Correlation '{correlation_id}' not found")
    return to_dict(row)
