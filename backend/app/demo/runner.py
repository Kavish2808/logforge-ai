"""Demo Mode orchestrator: the full unknown-vendor -> v1 -> drift -> learning
-> v2 -> rollback lifecycle, driven ONLY through the public HTTP API.

It never imports a repository or service: `client` is any object with
httpx-style `get`/`post` (an `httpx.Client` against the running stack, or
FastAPI's `TestClient` in tests). Human decisions (approvals, drift review,
activation, rollback) go through `decide`, which may pause for an operator;
each one is recorded by the backend with approved_by/by = DEMO_OPERATOR.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.demo import fixtures as fx

API = "/api/v1"
TOTAL = 23


class DemoFailure(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class DemoState:
    probe_id: str | None = None
    session_id: str | None = None
    proposal_version: int | None = None
    v1_event_ids: list[str] = field(default_factory=list)
    malformed_id: str | None = None
    drift_id: str | None = None
    learning_id: str | None = None
    learning_proposal_version: int | None = None
    check_v2: dict | None = None
    check_v1: dict | None = None
    post_rollback: dict | None = None
    results: list[tuple[int, str, bool, str]] = field(default_factory=list)


def _auto(prompt: str) -> None:  # non-interactive: the operator pre-approved the scripted run
    return None


class DemoRunner:
    def __init__(self, client: Any, *, decide: Callable[[str], None] = _auto, out: Callable[[str], None] = print):
        self.c = client
        self.decide = decide
        self.out = out
        self.s = DemoState()

    # ------------------------------------------------------------------ http
    def _call(self, method: str, path: str, body: dict | None = None, expect: tuple[int, ...] = (200, 201)) -> Any:
        resp = self.c.post(API + path, json=body or {}) if method == "POST" else self.c.get(API + path)
        if resp.status_code not in expect:
            try:
                err = resp.json().get("error", {})
                detail = f"{err.get('code')}: {err.get('message')}"
            except Exception:  # noqa: BLE001
                detail = resp.text[:300]
            raise DemoFailure(f"{method} {API}{path} -> HTTP {resp.status_code} ({detail})",
                              "Is the stack running and migrated? Check `docker compose logs backend`.")
        return resp.json()

    def _ingest(self, key: str) -> dict:
        f = fx.BY_KEY[key]
        ev = self._call("POST", "/ingest", {"raw_log": f.raw})
        if ev["raw_event"] != f.raw or ev["raw_hash"] != hashlib.sha256(f.raw.encode()).hexdigest():
            raise DemoFailure(f"{key}: raw log or SHA-256 not preserved exactly", "This would be a pipeline defect.")
        return ev

    @staticmethod
    def _check(cond: bool, message: str, hint: str = "") -> None:
        if not cond:
            raise DemoFailure(message, hint)

    # ---------------------------------------------------------------- stages
    def stage_reset(self) -> str:
        r = self._call("POST", "/demo/reset")
        d = r["deleted"]
        return (f"removed demo-owned rows only ({d['events']} events, {d['onboarding_sessions']} onboarding, "
                f"{d['adapter_versions']} adapter versions, {d['learning_sessions']} learning); non-demo rows unchanged")

    def stage_unknown(self) -> str:
        ev = self._ingest("unknown_probe")
        self._check(ev["status"] == "FAILED", f"unknown-vendor probe was {ev['status']}, expected FAILED",
                    f"Another adapter already matches vendor=LogForgeDemo logs ({ev.get('adapter_id')}); reset the demo.")
        self.s.probe_id = ev["event_id"]
        return f"event {ev['event_id']} FAILED (format {ev['format_detected']}); raw + SHA-256 preserved"

    def stage_session(self) -> str:
        samples = [f.raw for f in fx.of_kind("SAMPLE_V1")]
        s = self._call("POST", "/onboarding/sessions", {"name": fx.DEMO_ID, "samples": samples, "event_ids": [self.s.probe_id]})
        self.s.session_id = s["id"]
        self._check(s["sample_count"] == len(samples) + 1, f"session holds {s['sample_count']} samples, expected {len(samples) + 1}")
        return f"session {s['id']}: {len(samples)} pasted samples + the failed probe event = {s['sample_count']} samples ({s['status']})"

    def stage_analyze(self) -> str:
        s = self._call("GET", f"/onboarding/sessions/{self.s.session_id}")
        a = s["analysis"]
        self._check(a.get("dominant_format") == "kv", f"dominant format is {a.get('dominant_format')}, expected kv")
        ind = (a.get("vendor_indicators") or [{}])[0]
        self._check(ind.get("field") == "vendor", f"strongest identity indicator is {ind}, expected vendor")
        return (f"format kv in {a['parsed_in_dominant_format']}/{a['sample_count']} samples, {len(a['fields'])} fields, "
                f"identity {ind['field']}={ind['value']}")

    def stage_suggest(self) -> str:
        s = self._call("POST", f"/onboarding/sessions/{self.s.session_id}/suggest", {"provider": "offline"})
        self._check(s["status"] != "SUGGESTION_FAILED", f"suggestion failed: {s.get('suggestion_error')}")
        self._check(s["proposal_source"] == "offline", f"proposal source is {s['proposal_source']}, expected offline")
        self.s.proposal_version = s["proposal_version"]
        p = s["proposal"]
        return f"offline analyzer (deterministic, no LLM) proposed {p['vendor']}/{p['product']} {p['format']} with {len(p['mappings'])} mappings"

    def stage_sandbox(self) -> str:
        s = self._call("GET", f"/onboarding/sessions/{self.s.session_id}")
        v = s["validation"] or {}
        m = v.get("metrics") or {}
        self._check(v.get("result") == "PASSED", f"sandbox result {v.get('result')}: {v.get('reasons')}")
        self._check(s["activation"]["state"] == "NOT_ACTIVE_AWAITING_APPROVAL", f"activation state is {s['activation']['state']}",
                    "Nothing may be active before the human approval.")
        return (f"PASSED: match {m['matched_samples']}/{m['total_samples']}, coverage {m['mapping_coverage']:.0%}; "
                "adapter NOT active (awaiting human approval)")

    def stage_approve_v1(self) -> str:
        self.decide("Approve the onboarding proposal and activate adapter v1")
        r = self._call("POST", f"/onboarding/sessions/{self.s.session_id}/approve", {
            "proposal_version": self.s.proposal_version, "adapter_id": fx.DEMO_SOURCE_KEY,
            "approved_by": fx.DEMO_OPERATOR, "note": "demo: human approval of the offline proposal"})
        return f"approved by {fx.DEMO_OPERATOR}: session {r['session']['status']}"

    def stage_v1_active(self) -> str:
        a = self._call("GET", f"/onboarding/adapters/{fx.DEMO_SOURCE_KEY}")
        versions = [(v["version"], v["status"]) for v in a["versions"]]
        self._check(versions == [(1, "ACTIVE")], f"adapter versions {versions}, expected [(1, ACTIVE)]")
        return f"{fx.DEMO_SOURCE_KEY} v1 ACTIVE"

    def stage_ingest_v1(self) -> str:
        for f in fx.of_kind("EVENT_V1"):
            self.s.v1_event_ids.append(self._ingest(f.key)["event_id"])
        self.s.malformed_id = self._ingest("malformed")["event_id"]
        return f"{len(self.s.v1_event_ids)} v1 logs + 1 malformed record ingested"

    def stage_verify_v1(self) -> str:
        for eid in self.s.v1_event_ids:
            ev = self._call("GET", f"/events/{eid}")
            self._check(ev["status"] == "SUCCESS" and ev["adapter_version"] == "1",
                        f"v1 event {eid} is {ev['status']} via v{ev['adapter_version']}")
            self._check(isinstance((ev.get("network") or {}).get("dst_port"), int), f"v1 event {eid} has no normalized dst_port")
        bad = self._call("GET", f"/events/{self.s.malformed_id}")
        self._check(bad["status"] == "FAILED" and bad["raw_event"] == fx.BY_KEY["malformed"].raw,
                    f"malformed record is {bad['status']}; expected FAILED with raw preserved")
        return f"{len(self.s.v1_event_ids)}/{len(self.s.v1_event_ids)} SUCCESS via v1 (network.dst_port normalized); malformed FAILED, raw kept"

    def stage_ingest_drift(self) -> str:
        ev = self._ingest("drift_trigger")
        self.s.drift_id = ev["event_id"]
        return f"v2-structure log {ev['event_id']} ingested (dst_port -> dst_port_number, + policy_id, + zone)"

    def stage_verify_drift(self) -> str:
        ev = self._call("GET", f"/events/{self.s.drift_id}")
        d = ev["processing_metadata"].get("drift") or {}
        self._check(ev["status"] == "UNDER_REVIEW" and d.get("status") == "DRIFT",
                    f"drift event is {ev['status']}/{d.get('status')}, expected UNDER_REVIEW/DRIFT")
        diff = d.get("differences") or {}
        return (f"UNDER_REVIEW: DRIFT severity {d.get('severity')}, similarity {d.get('similarity'):.2f} < {d.get('threshold')}; "
                f"+{diff.get('added_fields')} -{diff.get('removed_fields')}")

    def stage_accept_drift(self) -> str:
        self.decide("Accept the structural drift as a legitimate variant")
        r = self._call("POST", f"/events/{self.s.drift_id}/drift/accept",
                       {"mode": "add_variant", "note": "demo: firmware update renamed dst_port, added policy_id and zone"})
        return f"accepted as variant; event now {r['event']['status']}"

    def stage_propose(self) -> str:
        for f in fx.of_kind("EVIDENCE_V2"):
            ev = self._ingest(f.key)
            self._check(ev["status"] != "UNDER_REVIEW", f"{f.key} was held for review after the variant was accepted")
        L = self._call("POST", f"/events/{self.s.drift_id}/learning/propose", {"assistant": "offline", "requested_by": fx.DEMO_OPERATOR})
        self.s.learning_id = L["id"]
        self.s.learning_proposal_version = L["proposal_version"]
        p = L["proposal"] or {}
        return (f"session {L['id']} ({L['status']}, risk {L['risk']}): remaps {[(r['from_field'], r['to_field']) for r in p.get('remaps', [])]}, "
                f"adds {[m['raw_field'] for m in p.get('add_mappings', [])]}, source '{L['proposal_source']}'")

    def stage_validate_learning(self) -> str:
        L = self._call("GET", f"/learning/sessions/{self.s.learning_id}")
        v = L["validation"] or {}
        self._check(v.get("result") == "PASSED" and L["status"] == "VALIDATED",
                    f"learning validation {v.get('result')} / {L['status']}: {v.get('reasons')}")
        n, h = v["new_structure"], v["historical"]
        return (f"PASSED: new structure {n['matched_samples']}/{n['total_samples']}, {h['total_samples']} historical samples, "
                f"{len(v.get('regressions') or [])} regressions")

    def stage_approve_v2(self) -> str:
        self.decide("Approve the learned adapter v2 (not activated yet)")
        L = self._call("POST", f"/learning/sessions/{self.s.learning_id}/approve", {
            "proposal_version": self.s.learning_proposal_version, "approved_by": fx.DEMO_OPERATOR,
            "note": "demo: human approval of the learned mapping", "confirm_supersede": False, "activate": False})
        self._check(L["status"] == "APPROVED", f"learning session is {L['status']} after approval")
        return f"APPROVED by {fx.DEMO_OPERATOR}; v{L['target_version']} will only be created when a human activates it"

    def stage_activate_v2(self) -> str:
        self.decide("Activate adapter v2")
        L = self._call("POST", f"/learning/sessions/{self.s.learning_id}/activate", {"activated_by": fx.DEMO_OPERATOR})
        a = self._call("GET", f"/onboarding/adapters/{fx.DEMO_SOURCE_KEY}")
        versions = [(v["version"], v["status"]) for v in a["versions"]]
        self._check(L["status"] == "ACTIVE" and versions == [(1, "SUPERSEDED"), (2, "ACTIVE")], f"after activation: {L['status']} {versions}")
        return "v2 ACTIVE, v1 SUPERSEDED (kept)"

    def stage_check_v2(self) -> str:
        ev = self._ingest("check_v2")
        self.s.check_v2 = ev
        return f"new structure -> {ev['status']} via v{ev['adapter_version']}"

    def stage_check_v1(self) -> str:
        ev = self._ingest("check_v1")
        self.s.check_v1 = ev
        return f"old structure -> {ev['status']} via v{ev['adapter_version']}"

    def stage_verify_both(self) -> str:
        n, o = self.s.check_v2, self.s.check_v1
        for label, ev, key, raw_field in (("new", n, "check_v2", "dst_port_number"), ("old", o, "check_v1", "dst_port")):
            self._check(ev["status"] == "SUCCESS" and ev["adapter_version"] == "2", f"{label} structure is {ev['status']} via v{ev['adapter_version']}")
            expected = int(re.search(rf"\b{raw_field}=(\d+)", fx.BY_KEY[key].raw).group(1))
            got = (ev.get("network") or {}).get("dst_port")
            self._check(got == expected, f"{label} structure: network.dst_port is {got}, expected {expected} from '{raw_field}'")
        return (f"both SUCCESS via v2: new dst_port_number -> network.dst_port={n['network']['dst_port']}, "
                f"old dst_port -> network.dst_port={o['network']['dst_port']}")

    def stage_rollback(self) -> str:
        self.decide("Roll back adapter v2 to v1")
        L = self._call("POST", f"/learning/sessions/{self.s.learning_id}/rollback", {"reason": "demo: rollback drill", "requested_by": fx.DEMO_OPERATOR})
        self._check(L["status"] == "ROLLED_BACK", f"learning session is {L['status']} after rollback")
        return "learning session ROLLED_BACK"

    def stage_verify_rollback(self) -> str:
        a = self._call("GET", f"/onboarding/adapters/{fx.DEMO_SOURCE_KEY}")
        versions = [(v["version"], v["status"]) for v in a["versions"]]
        self._check(versions == [(1, "ACTIVE"), (2, "ROLLED_BACK")], f"versions after rollback: {versions}")
        ev = self._ingest("post_rollback_v1")
        self.s.post_rollback = ev
        self._check(ev["status"] == "SUCCESS" and ev["adapter_version"] == "1", f"post-rollback v1 log is {ev['status']} via v{ev['adapter_version']}")
        return "v1 ACTIVE, v2 ROLLED_BACK (preserved); a new v1 log parses via v1 again"

    def stage_summary(self) -> str:
        st = self._call("GET", "/demo/status")
        self._check(st["complete"], f"demo status incomplete at step {st['next']}")
        tl = self._call("GET", f"/views/sources/{fx.DEMO_SOURCE_KEY}/timeline")
        L = self._call("GET", f"/learning/sessions/{self.s.learning_id}")
        lineage = self._call("GET", f"/views/events/{self.s.check_v2['event_id']}/lineage")
        self._check(lineage["integrity"]["verified"] and lineage["nothing_silently_discarded"], "lineage of the v2 check event is not intact")
        kinds = [e["kind"] for e in tl["entries"]]
        return (f"{len(st['events'])} demo events; {len(kinds)} timeline entries; decisions "
                f"{[d['action'] for d in L['decisions']]}; lineage verified")

    STAGES: list[tuple[str, str]] = [
        ("RESET DEMO STATE", "stage_reset"),
        ("UNKNOWN VENDOR LOG ARRIVES", "stage_unknown"),
        ("CREATE ONBOARDING SESSION WITH 13 SAMPLES", "stage_session"),
        ("ANALYZE SAMPLES", "stage_analyze"),
        ("GENERATE OFFLINE SUGGESTION", "stage_suggest"),
        ("SANDBOX VALIDATION", "stage_sandbox"),
        ("HUMAN APPROVAL (ONBOARDING)", "stage_approve_v1"),
        ("ADAPTER V1 ACTIVE", "stage_v1_active"),
        ("INGEST V1 EVENTS", "stage_ingest_v1"),
        ("VERIFY SUCCESS + NORMALIZATION + RAW HASH", "stage_verify_v1"),
        ("INGEST V2 STRUCTURE", "stage_ingest_drift"),
        ("VERIFY UNDER_REVIEW / DRIFT", "stage_verify_drift"),
        ("HUMAN ACCEPTS DRIFT", "stage_accept_drift"),
        ("PROPOSE LEARNING", "stage_propose"),
        ("VALIDATE LEARNING (SANDBOX REGRESSION)", "stage_validate_learning"),
        ("HUMAN APPROVAL (LEARNING)", "stage_approve_v2"),
        ("HUMAN ACTIVATES ADAPTER V2", "stage_activate_v2"),
        ("INGEST V2 EVENT", "stage_check_v2"),
        ("INGEST OLD V1 EVENT", "stage_check_v1"),
        ("VERIFY BOTH WORK", "stage_verify_both"),
        ("HUMAN ROLLS BACK V2", "stage_rollback"),
        ("VERIFY VERSION STATE", "stage_verify_rollback"),
        ("FINAL DEMO SUMMARY", "stage_summary"),
    ]

    def run(self) -> bool:
        for i, (title, method) in enumerate(self.STAGES, start=1):
            self.out(f"[{i}/{TOTAL}] {title}")
            try:
                detail = getattr(self, method)()
            except DemoFailure as exc:
                self.s.results.append((i, title, False, str(exc)))
                self.out(f"[FAIL] {exc}")
                if exc.hint:
                    self.out(f"       hint: {exc.hint}")
                self.out(f"       Demo stopped at stage {i}. Inspect it in the console, then rerun (the run starts with a scoped reset).")
                return False
            self.s.results.append((i, title, True, detail))
            self.out(f"[PASS] {detail}")
        self.out(f"\nDEMO COMPLETE: {TOTAL}/{TOTAL} stages passed. Open the console Demo page or #/evolution/{fx.DEMO_SOURCE_KEY}.")
        return True


def reset(client: Any, out: Callable[[str], None] = print) -> dict:
    resp = client.post(f"{API}/demo/reset", json={})
    if resp.status_code != 200:
        out(f"[FAIL] reset refused: HTTP {resp.status_code} {resp.text[:300]}")
        return {}
    r = resp.json()
    out(f"[PASS] demo reset: deleted {r['deleted']}")
    out(f"       non-demo rows before {r['non_demo_rows']['before']}")
    out(f"       non-demo rows after  {r['non_demo_rows']['after']}")
    return r
