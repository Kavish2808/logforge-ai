"""E2E finding D1 (syslog-wrapped LEEF) - intended behavior, pinned with the exact E2E input.

Contract (docs/ARCHITECTURE.md "Syslog envelope priority, including Syslog-wrapped LEEF";
leef_parser module docstring; test_format_priority TEST 3): the syslog envelope keeps priority, so a
syslog-wrapped LEEF event is a syslog event and its LEEF attributes are NOT extracted into typed
fields. What must hold instead: nothing is lost - the complete LEEF payload stays in the normalized
message, raw bytes and SHA-256 are unchanged, reparse is deterministic - while the same payload
without the envelope is parsed natively as LEEF."""
import hashlib

from app.pipeline.detector.format_detector import detect_format, detect_secondary_format
from app.schema.ocsf import FormatType

API = "/api/v1"
LEEF = "LEEF:2.0|Lancope|StealthWatch|1.0|41|^|src=10.0.1.8^dst=10.0.0.5^sev=5^srcPort=81^dstPort=21"
WRAPPED = "<13>Sep 28 10:07:00 qradar " + LEEF


def ingest(client, raw):
    r = client.post(f"{API}/ingest", json={"raw_log": raw})
    assert r.status_code == 201, r.text
    return r.json()


def test_wrapped_leef_stays_syslog_and_preserves_the_whole_leef_payload(client):
    assert detect_format(WRAPPED) == FormatType.SYSLOG
    e = ingest(client, WRAPPED)
    assert (e["format_detected"], e["adapter_id"]) == ("syslog", "syslog_generic")
    assert e["raw_event"] == WRAPPED and e["raw_hash"] == hashlib.sha256(WRAPPED.encode()).hexdigest()
    # syslog parses "LEEF" as the process tag and the rest as the message: tag + ":" + message == payload
    ne = e["normalized_event"]
    assert ne["process"]["name"] + ":" + ne["event_message"] == LEEF
    assert e["network"]["src_ip"] is None  # documented: envelope priority, no LEEF attribute extraction
    lineage = client.get(f"{API}/views/events/{e['event_id']}/lineage").json()
    assert lineage["nothing_silently_discarded"] is True
    again = client.post(f"{API}/events/{e['event_id']}/reprocess").json()["event"]
    assert again["normalized_event"] == e["normalized_event"] and again["raw_hash"] == e["raw_hash"]


def test_the_same_payload_without_envelope_is_native_leef(client):
    assert detect_format(LEEF) == FormatType.UNKNOWN and detect_secondary_format(LEEF) == FormatType.LEEF
    e = ingest(client, LEEF)
    assert (e["format_detected"], e["adapter_id"], e["status"]) == ("leef", "leef_generic", "SUCCESS")
    assert e["network"]["src_ip"] == "10.0.1.8" and e["network"]["dst_port"] == 21
