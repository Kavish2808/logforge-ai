"""Regression tests for the Phase 0-4 duplicate-event defect: a value that
cannot be converted into a typed OCSF group field used to be committed as a
PARTIAL event, then fail response validation, so the ingestion safety net
stored a second raw-only FAILED row — and every later read of the first row
(GET /events, GET /events/{id}, reprocess) returned 500."""
import hashlib
import json

import pytest
from sqlalchemy import text

from app.services import ingestion_service

API = "/api/v1"

FORTINET_DASH = ('<189>Jan 18 12:00:00 FGT100E FORTIGATE: date=2026-01-18 time=12:00:00 devname="FGT100E" '
                 'srcip=10.0.0.15 srcport=- dstip=8.8.8.8 dstport=- proto=1 action="accept" level="notice" msg="icmp"')
CEF_ABOBJECT = "CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|5|src=10.0.0.30 dst=93.184.216.34 spt=abobject dpt=443 act=allow"


def rows(db_session) -> int:
    return db_session.execute(text("SELECT count(*) FROM events")).scalar_one()


def ingest(client, raw):
    resp = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.parametrize("raw,field,value,adapter,kept", [
    (FORTINET_DASH, "dstport", "-", "fortinet", {"src_ip": "10.0.0.15", "dst_ip": "8.8.8.8"}),          # A
    (CEF_ABOBJECT, "spt", "abobject", "paloalto_cef", {"src_ip": "10.0.0.30", "dst_port": 443}),        # B
    (json.dumps({"message": "login", "user": 12345}), "user", 12345, "json_generic", {}),
    (json.dumps({"message": "x", "dst_port": {"p": 1}}), "dst_port", {"p": 1}, "json_generic", {}),
])
def test_unconvertible_typed_value_creates_one_readable_partial_event(client, db_session, raw, field, value, adapter, kept):
    before = rows(db_session)
    body = ingest(client, raw)

    assert rows(db_session) == before + 1                                  # C: exactly one row
    assert body["status"] == "PARTIAL" and body["adapter_id"] == adapter   # D
    assert body["raw_event"] == raw                                        # D: raw preserved
    assert body["raw_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert body["extensions"][field] == value                              # E: bad value preserved
    assert any(f"{field}:" in w and "preserved in extensions" in w for w in body["warnings"])
    for k, v in kept.items():                                              # I: good fields still normalized
        assert body["network"][k] == v

    listing = client.get(f"{API}/events", params={"limit": 50})           # F
    assert listing.status_code == 200
    assert body["event_id"] in [e["event_id"] for e in listing.json()["items"]]
    one = client.get(f"{API}/events/{body['event_id']}")                  # G
    assert one.status_code == 200 and one.json()["extensions"][field] == value
    re = client.post(f"{API}/events/{body['event_id']}/reprocess")         # H
    assert re.status_code == 200 and re.json()["event"]["status"] == "PARTIAL"
    assert re.json()["event"]["event_id"] == body["event_id"]
    assert rows(db_session) == before + 1                                  # still no duplicate


def test_fortinet_dash_ports_both_preserved(client):
    body = ingest(client, FORTINET_DASH)
    assert body["extensions"]["srcport"] == "-" and body["extensions"]["dstport"] == "-"
    assert body["network"]["src_port"] is None and body["network"]["dst_port"] is None
    assert body["vendor"] == "Fortinet" and body["event_action"] == "accept"


def test_valid_typed_values_still_normalize(client, db_session):                  # I
    before = rows(db_session)
    body = ingest(client, FORTINET_DASH.replace("srcport=-", "srcport=51422").replace("dstport=-", "dstport=53"))
    assert body["status"] == "SUCCESS" and body["warnings"] == []
    assert body["network"]["src_port"] == 51422 and body["network"]["dst_port"] == 53
    assert "dstport" not in body["extensions"]
    assert rows(db_session) == before + 1


def test_batch_with_bad_values_has_one_row_per_log(client, db_session):
    before = rows(db_session)
    logs = [{"raw_log": FORTINET_DASH}, {"raw_log": CEF_ABOBJECT}, {"raw_log": "garbage"}]
    body = client.post(f"{API}/ingest/batch", json={"logs": logs}).json()
    assert body["total"] == 3 and body["partial_count"] == 2 and body["failed_count"] == 1
    assert rows(db_session) == before + 3


def test_response_validation_failure_never_writes_two_rows(client, db_session, monkeypatch):
    """Defense in depth: even if some future path produced an unreadable
    event, the request stores exactly one (raw-only FAILED) row."""
    real = ingestion_service._pipeline_result_fields

    def poisoned(result):
        fields = real(result)
        fields["network"] = {"dst_port": "definitely-not-an-int"}
        return fields

    monkeypatch.setattr(ingestion_service, "_pipeline_result_fields", poisoned)
    before = rows(db_session)
    body = ingest(client, FORTINET_DASH)
    assert rows(db_session) == before + 1
    assert body["status"] == "FAILED" and body["raw_event"] == FORTINET_DASH
    assert client.get(f"{API}/events").status_code == 200


def test_reprocess_repairs_a_row_poisoned_before_the_fix(client, db_session):
    body = ingest(client, FORTINET_DASH)
    db_session.execute(text("UPDATE events SET network = CAST(:n AS jsonb) WHERE event_id = :i"),
                       {"n": json.dumps({"dst_port": "-"}), "i": body["event_id"]})
    db_session.commit()  # the row now has the unreadable pre-fix shape
    fixed = client.post(f"{API}/events/{body['event_id']}/reprocess")
    assert fixed.status_code == 200 and fixed.json()["event"]["extensions"]["dstport"] == "-"
    assert client.get(f"{API}/events/{body['event_id']}").status_code == 200
    assert client.get(f"{API}/events").status_code == 200
