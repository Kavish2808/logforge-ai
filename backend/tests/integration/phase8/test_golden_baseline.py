"""Phase 8 Step 5: golden + current dual baseline and poisoning protection,
through the real API, Phase 7 governance and the Phase 5/6 workflows."""
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.phase8 import register as p8
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import (  # noqa: F401
    API,
    PASSWORD,
    _phase7_defaults,
    auth,
    ingest,
    isolated_stores,
    login,
    users,
)
from tests.integration.test_learning_flow import ADAPTER, base_log, with_added

GOLDEN = f"{API}/golden-baselines/{ADAPTER}"


@pytest.fixture(autouse=True)
def _drift_settings(monkeypatch):
    s = get_settings()
    for name, value in (("drift_enabled", True), ("drift_similarity_threshold", 0.85), ("onboarding_min_match_rate", 0.9),
                        ("onboarding_reject_below_match_rate", 0.5), ("onboarding_min_mapping_coverage", 0.3)):
        monkeypatch.setattr(s, name, value)


@pytest.fixture()
def source(client):
    sess = client.post(f"{API}/onboarding/sessions", json={"samples": [base_log(i) for i in range(12)]}).json()
    sess = client.post(f"{API}/onboarding/sessions/{sess['id']}/suggest", json={"provider": "offline"}).json()
    r = client.post(f"{API}/onboarding/sessions/{sess['id']}/approve", json={"proposal_version": sess["proposal_version"]})
    assert r.status_code == 200
    return [ingest(client, base_log(100 + i)) for i in range(5)]


@pytest.fixture()
def admin2(client, users):
    r = client.post(f"{API}/auth/users", json={"username": "admin2", "password": PASSWORD, "role": "SOC_ADMIN"},
                    headers=users["admin"])
    assert r.status_code == 201, r.text
    return auth(login(client, "admin2"))


def pin(client, headers, note="trusted reference, verified against vendor docs"):
    return client.post(GOLDEN, json={"note": note}, headers=headers)


def far_from_golden(i: int) -> str:
    """Routes to the onboarded adapter (vendor=ACMEFW) but shares little structure with it."""
    return f"vendor=ACMEFW " + " ".join(f"zz{k}=v{i}" for k in range(9))


def added(i: int, k: int) -> str:
    return base_log(i) + f" a{k}=1 b{k}=2 c{k}=3"


def drifted(client, raw):
    e = ingest(client, raw)
    assert e["status"] == "UNDER_REVIEW", e["processing_metadata"].get("drift")
    return e


def accept(client, event, headers=None, mode="add_variant", note=None):
    body = {"mode": mode, **({"note": note} if note is not None else {})}
    return client.post(f"{API}/events/{event['event_id']}/drift/accept", json=body, headers=headers)


def audits(client, headers, action):
    return client.get(f"{API}/governance/audit", params={"action": action}, headers=headers).json()["items"]


def comparisons(client):
    return client.get(f"{API}/golden-baselines/comparisons").json()["items"]


def golden_row():
    with TestSessionLocal() as db:
        return db.execute(text("SELECT id, version, status, fingerprint, statistical_profile, "
                               "derived_from_baseline_version, approved_by, note, evidence FROM golden_baselines "
                               "WHERE source_key = :s ORDER BY version"), {"s": ADAPTER}).all()


def add_history(n: int):
    """Accepted-change history rows (as Phase 5/6 write them) — for the count rule only."""
    with TestSessionLocal() as db:
        for k in range(n):
            db.execute(text("INSERT INTO source_baseline_history (source_key, version, action, note) "
                            "VALUES (:s, :v, 'VARIANT_ADDED', 'test history')"), {"s": ADAPTER, "v": 1000 + k})
        db.commit()


# --- no golden: fully compatible ------------------------------------------------------------------------------


def test_no_golden_does_not_block_or_invent(client, source):
    r = accept(client, drifted(client, far_from_golden(1)))
    assert r.status_code == 200
    entry = audits(client, None, "DRIFT_ADD_VARIANT")[0]
    assert entry["decision"] == "SUCCESS" and "guards" not in entry["details"]  # identical Phase 7 audit
    assert comparisons(client) == [] and golden_row() == []
    assert client.get(f"{API}/golden-baselines").json()["items"] == []
    assert client.get(GOLDEN).status_code == 404


# --- golden CRUD under governance ------------------------------------------------------------------------------


def test_pin_requires_authenticated_soc_admin_and_note(client, source, users):
    assert pin(client, None).status_code == 401
    assert pin(client, users["eng1"]).status_code == 403
    assert pin(client, users["analyst"]).status_code == 403
    r = pin(client, users["admin"], note="   ")
    assert r.status_code == 422 and "note" in r.json()["error"]["message"]
    assert golden_row() == []
    r = pin(client, users["admin"])
    assert r.status_code == 201, r.text
    g = r.json()
    baseline = client.get(f"{API}/drift/baselines/{ADAPTER}").json()
    assert g["version"] == 1 and g["status"] == "ACTIVE" and g["approved_by"] == "admin"
    assert g["approved_role"] == "SOC_ADMIN"
    assert g["fingerprint"]["reference"] == baseline["fingerprint"]
    assert g["derived_from_baseline_version"] == baseline["version"]
    assert g["evidence"]["adapter_id"] == ADAPTER and g["evidence"]["adapter_version"] == "1"
    assert g["statistical_profile"]["sufficient"] is False  # 5 events < 200: recorded, not invented
    assert pin(client, users["admin"]).status_code == 409  # re-pin is an explicit PUT
    decisions = [(a["decision"], a["actor"]) for a in reversed(audits(client, users["admin"], "GOLDEN_BASELINE_PIN"))]
    assert decisions == [("DENIED", "anonymous"), ("DENIED", "eng1"), ("DENIED", "analyst"), ("DENIED", "admin"),
                         ("SUCCESS", "admin"), ("DENIED", "admin")]
    detail = client.get(GOLDEN).json()
    assert detail["active"]["id"] == g["id"] and len(detail["versions"]) == 1


def test_pin_without_phase5_baseline_is_refused(client, users):
    r = client.post(f"{API}/golden-baselines/no_such_source", json={"note": "x"}, headers=users["admin"])
    assert r.status_code == 409 and "never invented" in r.json()["error"]["message"]


# --- guard decisions ------------------------------------------------------------------------------------------


def test_normal_action_is_allowed_and_evidenced(client, source, users):
    pin(client, users["admin"])
    before = golden_row()
    r = accept(client, drifted(client, with_added(1)))
    assert r.status_code == 200
    entry = audits(client, users["admin"], "DRIFT_ADD_VARIANT")[0]
    [g] = entry["details"]["guards"]
    assert g["guard"] == "golden_poisoning" and g["verdict"] == "ALLOW"
    assert g["evidence"]["new_vs_golden_similarity"] >= 0.70 and g["evidence"]["steps_since_golden"] == 0
    [c] = comparisons(client)
    assert c["id"] == g["evidence"]["comparison_id"] and c["decision"] == "ALLOWED" and c["poisoning_risk"] is False
    assert golden_row() == before


def test_low_golden_similarity_requires_elevated_review(client, source, users):
    pin(client, users["admin"])
    event = drifted(client, far_from_golden(1))
    r = accept(client, event)
    assert r.status_code == 403 and "Elevated review required" in r.json()["error"]["message"]
    assert accept(client, event, users["eng1"], note="looks fine").status_code == 403  # not SOC_ADMIN
    r = accept(client, event, users["admin"])
    assert r.status_code == 403 and "written note" in r.json()["error"]["message"]
    assert client.get(f"{API}/events/{event['event_id']}").json()["status"] == "UNDER_REVIEW"  # nothing changed
    r = accept(client, event, users["admin"], note="vendor firmware 9 changed the log schema; confirmed")
    assert r.status_code == 200
    entries = audits(client, users["admin"], "DRIFT_ADD_VARIANT")
    assert [e["decision"] for e in reversed(entries)] == ["DENIED", "DENIED", "DENIED", "SUCCESS"]
    ok = entries[0]
    assert ok["actor"] == "admin" and ok["details"]["guards"][0]["verdict"] == "ELEVATED"
    assert ok["details"]["guards"][0]["evidence"]["reasons"] == ["GOLDEN_SIMILARITY_BELOW_THRESHOLD"]
    assert ok["details"]["request"]["note"].startswith("vendor firmware 9")
    assert {c["decision"] for c in comparisons(client)} == {"ELEVATED"}
    assert client.get(f"{API}/governance/audit/verify", headers=users["admin"]).json()["valid"] is True


def test_replace_baseline_is_guarded_too(client, source, users):
    pin(client, users["admin"])
    event = drifted(client, far_from_golden(2))
    assert accept(client, event, users["admin"], mode="replace_baseline").status_code == 403
    r = accept(client, event, users["admin"], mode="replace_baseline", note="full vendor schema migration")
    assert r.status_code == 200
    assert audits(client, users["admin"], "DRIFT_REPLACE_BASELINE")[0]["details"]["guards"][0]["verdict"] == "ELEVATED"


def test_more_than_five_accepted_changes_since_golden_is_elevated(client, source, users):
    pin(client, users["admin"])
    before = golden_row()
    for k in range(6):  # six legitimate, individually small changes: each ALLOWED
        assert accept(client, drifted(client, added(200 + k, k))).status_code == 200
    seventh = drifted(client, added(300, 6))
    r = accept(client, seventh)
    assert r.status_code == 403 and "6 accepted baseline changes" in r.json()["error"]["message"]
    assert accept(client, seventh, users["admin"], note="batch of vendor additions reviewed").status_code == 200
    decisions = [c["decision"] for c in reversed(comparisons(client))]
    assert decisions == ["ALLOWED"] * 6 + ["ELEVATED", "ELEVATED"]
    elevated = comparisons(client)[0]
    assert elevated["steps_since_golden"] == 6 and elevated["risk_reasons"][0]["code"] == "TOO_MANY_CHANGES_SINCE_GOLDEN"
    # The CURRENT baseline evolved 7 times; the GOLDEN baseline did not change at all.
    assert client.get(f"{API}/drift/baselines/{ADAPTER}").json()["version"] == 8
    assert golden_row() == before
    cmp = client.get(f"{GOLDEN}/compare").json()
    assert cmp["steps_since_golden"] == 7 and cmp["next_change_would_be_elevated"] is True
    assert cmp["reasons"] == ["TOO_MANY_CHANGES_SINCE_GOLDEN"] and len(cmp["explanation"]) == 4


# --- learning activation, maker-checker ------------------------------------------------------------------------


def learning_session(client, users):
    event = drifted(client, with_added(1))
    assert accept(client, event, users["eng1"], note="legit change").status_code == 200
    r = client.post(f"{API}/events/{event['event_id']}/learning/propose", json={"assistant": "offline"},
                    headers=users["admin"])  # admin is the maker
    assert r.status_code == 201, r.text
    s = r.json()
    r = client.post(f"{API}/learning/sessions/{s['id']}/approve", json={"proposal_version": s["proposal_version"],
                                                                         "note": "reviewed"}, headers=users["eng1"])
    assert r.status_code == 200, r.text
    return s


def activate(client, s, headers, **body):
    return client.post(f"{API}/learning/sessions/{s['id']}/activate", json=body, headers=headers)


def test_elevated_learning_activation_respects_maker_checker(client, source, users, admin2):
    pin(client, users["admin"])
    s = learning_session(client, users)
    add_history(5)  # 1 real + 5 recorded accepted changes since the golden -> 6 > 5
    before = golden_row()
    r = activate(client, s, users["admin"], note="ship it")
    assert r.status_code == 403 and "Maker-checker" in r.json()["error"]["message"]  # Phase 7 rule first
    assert activate(client, s, users["eng2"], note="ship it").status_code == 403  # elevated: SOC_ADMIN only
    r = activate(client, s, admin2)
    assert r.status_code == 403 and "written note" in r.json()["error"]["message"]
    r = activate(client, s, admin2, note="independent SOC_ADMIN review of the learned mapping")
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    entry = audits(client, users["admin"], "LEARNING_ACTIVATE")[0]
    assert entry["actor"] == "admin2" and entry["details"]["maker_checker"]["violation"] is False
    assert entry["details"]["guards"][0]["verdict"] == "ELEVATED"
    assert golden_row() == before  # learning moved the CURRENT baseline only


def test_approve_and_activate_in_one_call_is_guarded(client, source, users):
    pin(client, users["admin"])
    event = drifted(client, with_added(1))
    accept(client, event, users["eng1"], note="legit")
    s = client.post(f"{API}/events/{event['event_id']}/learning/propose", json={"assistant": "offline"},
                    headers=users["analyst"]).json()
    add_history(5)
    body = {"proposal_version": s["proposal_version"], "activate": True, "note": "approve and ship"}
    r = client.post(f"{API}/learning/sessions/{s['id']}/approve", json=body, headers=users["eng1"])
    assert r.status_code == 403 and "Elevated review required" in r.json()["error"]["message"]
    r = client.post(f"{API}/learning/sessions/{s['id']}/approve", json=body, headers=users["admin"])
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"


# --- re-pin / retire ---------------------------------------------------------------------------------------------


def test_repin_is_versioned_and_maker_checked_and_retire_is_audited(client, source, users, admin2):
    first = pin(client, users["admin"]).json()
    assert accept(client, drifted(client, with_added(1)), users["admin"], note="accepted by admin").status_code == 200
    r = client.put(GOLDEN, json={"note": "bless current"}, headers=users["admin"])
    assert r.status_code == 403 and "Maker-checker" in r.json()["error"]["message"]
    assert client.put(GOLDEN, json={"note": "bless", "expected_version": 7}, headers=admin2).status_code == 409
    r = client.put(GOLDEN, json={"note": "independent re-pin after review", "expected_version": 1}, headers=admin2)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["version"] == 2 and second["evidence"]["supersedes"] == {"id": first["id"], "version": 1}
    assert second["derived_from_baseline_version"] == first["derived_from_baseline_version"] + 1
    rows = golden_row()
    assert [(g.version, g.status) for g in rows] == [(1, "SUPERSEDED"), (2, "ACTIVE")]
    assert rows[0].fingerprint == first["fingerprint"]  # the superseded golden is kept verbatim
    assert client.post(f"{GOLDEN}/retire", json={"note": ""}, headers=admin2).status_code == 422
    r = client.post(f"{GOLDEN}/retire", json={"note": "source decommissioned"}, headers=admin2)
    assert r.status_code == 200 and r.json()["status"] == "RETIRED"
    actions = {a["action"] for a in client.get(f"{API}/governance/audit", params={"object_type": "golden_baseline"},
                                                headers=users["admin"]).json()["items"] if a["decision"] == "SUCCESS"}
    assert actions == {"GOLDEN_BASELINE_PIN", "GOLDEN_BASELINE_REPIN", "GOLDEN_BASELINE_RETIRE"}
    # With no active golden the guard is silent again.
    assert accept(client, drifted(client, far_from_golden(5))).status_code == 200


def test_compare_is_explainable_and_read_only(client, source, users):
    pin(client, users["admin"])
    before = golden_row()
    cmp = client.get(f"{GOLDEN}/compare").json()
    assert cmp["structural"]["similarity"] == 1.0 and cmp["steps_since_golden"] == 0
    assert cmp["next_change_would_be_elevated"] is False and cmp["statistical"]["compared"] is False
    assert "200 events" in cmp["statistical"]["reason"] and len(cmp["explanation"]) == 4
    assert golden_row() == before and comparisons(client) == []


def test_phase8_disabled_means_no_guard(client, source, users, monkeypatch):
    pin(client, users["admin"])
    monkeypatch.setattr(get_settings(), "phase8_enabled", False)
    p8.register_all()
    try:
        assert accept(client, drifted(client, far_from_golden(1))).status_code == 200
    finally:
        monkeypatch.undo()
        p8.register_all()
