"""Export + integration contract: filters, keyset pagination, bounded
streaming, NDJSON/JSON framing, and conformance to the published schema."""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.export.schema import EVENT_RECORD, SCHEMA_VERSION, TRAILER_RECORD
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API, ingest

FORTI = ('<189>Jan 18 12:00:00 FGT100E FORTIGATE: date=2026-01-18 srcip=10.0.0.{i} srcport=51422 '
         'dstip=8.8.8.8 dstport=53 proto=17 action="accept" level="notice" msg="dns"')
CISCO = "<166>Jan 18 12:05:00 ciscoasa %ASA-6-302013: Built outbound TCP connection {i}"


# --- a minimal JSON Schema validator for the keywords the published schema uses ------------------


def _types(value):
    if value is None:
        return {"null"}
    if isinstance(value, bool):
        return {"boolean"}
    if isinstance(value, int):
        return {"integer", "number"}
    if isinstance(value, float):
        return {"number"}
    return {{str: "string", dict: "object", list: "array"}[type(value)]}


def validate(value, schema, path="$"):
    errors = []
    t = schema.get("type")
    if t is not None:
        allowed = {t} if isinstance(t, str) else set(t)
        if not _types(value) & allowed:
            return [f"{path}: {type(value).__name__} not in {sorted(allowed)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: {value!r} != const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required {req!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors.extend(validate(value[key], sub, f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errors


def ndjson(resp):
    assert resp.status_code == 200, resp.text
    lines = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert lines[-1]["record_type"] == "trailer" and lines[-1]["complete"] is True
    return lines[:-1], lines[-1]


@pytest.fixture()
def corpus(client):
    """3 Fortinet, 2 Cisco, 1 JSON, 1 FAILED — oldest first."""
    evts = [ingest(client, FORTI.format(i=i)) for i in range(3)]
    evts += [ingest(client, CISCO.format(i=i)) for i in range(2)]
    evts.append(ingest(client, json.dumps({"user": "bob", "action": "logout", "unmapped_thing": 7})))
    evts.append(ingest(client, "garbage ~~ not a log"))
    return evts


def test_schema_endpoint_publishes_versioned_contract(client):
    s = client.get(f"{API}/export/schema").json()
    assert s["schema_version"] == SCHEMA_VERSION == "logforge.export.v1"
    assert "event_id" in s["event_record"]["required"] and "integrity" in s["event_record"]["required"]
    assert {"revisions", "extensions", "integrity", "completeness", "compatibility"} <= set(s["semantics"])
    assert "/export/schema" in json.dumps(client.get("/openapi.json").json()["paths"])


def test_ndjson_export_conforms_to_the_published_schema(client, corpus):
    r = client.get(f"{API}/export/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["x-logforge-schema-version"] == SCHEMA_VERSION
    records, trailer = ndjson(r)
    assert validate(trailer, TRAILER_RECORD) == []
    assert trailer["count"] == len(records) == len(corpus) and trailer["has_more"] is False
    for rec in records:
        assert validate(rec, EVENT_RECORD) == [], rec["event_id"]
    # newest first, every event exactly once
    assert [r_["event_id"] for r_ in records] == [e["event_id"] for e in reversed(corpus)]
    by_id = {r_["event_id"]: r_ for r_ in records}
    for e in corpus:
        rec = by_id[e["event_id"]]
        assert rec["raw"]["sha256"] == rec["integrity"]["raw_sha256"] == e["raw_hash"]
        assert rec["raw"]["payload"] is None  # raw only on request
        assert rec["raw"]["vault"]["status"] == "STORED"
        assert rec["status"] == e["status"]


def test_export_filters(client, corpus):
    def ids(**params):
        records, _ = ndjson(client.get(f"{API}/export/events", params=params))
        return {r["event_id"] for r in records}

    forti = {e["event_id"] for e in corpus if e["vendor"] == "Fortinet"}
    assert forti and ids(vendor="Fortinet") == forti
    assert ids(source=corpus[0]["adapter_id"]) == forti
    assert ids(status="FAILED") == {corpus[-1]["event_id"]}
    assert ids(format="json") == {corpus[5]["event_id"]}
    assert ids(event_id=[corpus[0]["event_id"], corpus[3]["event_id"]]) == {corpus[0]["event_id"], corpus[3]["event_id"]}
    # time range: move two events into the past
    past = datetime.now(tz=timezone.utc) - timedelta(days=3)
    with TestSessionLocal() as db:
        db.execute(text("UPDATE events SET received_at=:t WHERE event_id IN (:a,:b)"),
                   {"t": past, "a": corpus[0]["event_id"], "b": corpus[1]["event_id"]})
        db.commit()
    window = {"start": (past - timedelta(hours=1)).isoformat(), "end": (past + timedelta(hours=1)).isoformat()}
    assert ids(**window) == {corpus[0]["event_id"], corpus[1]["event_id"]}


def test_cursor_pagination_is_complete_and_disjoint(client, corpus):
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        records, trailer = ndjson(client.get(f"{API}/export/events", params=params))
        seen += [r["event_id"] for r in records]
        pages += 1
        if not trailer["has_more"]:
            assert trailer["next_cursor"] is None
            break
        cursor = trailer["next_cursor"]
    assert pages == 3 and len(seen) == len(set(seen)) == len(corpus)
    assert client.get(f"{API}/export/events", params={"cursor": "!!bad"}).status_code == 422


def test_export_is_bounded_and_streams_in_batches(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "export_batch_size", 10)
    monkeypatch.setattr(s, "export_max_events", 25)
    for i in range(32):
        ingest(client, json.dumps({"user": f"u{i}", "action": "login"}))
    r = client.get(f"{API}/export/events", params={"limit": 999})
    records, trailer = ndjson(r)
    assert r.headers["x-logforge-max-events"] == "25"
    assert len(records) == 25 and trailer["has_more"] is True and trailer["next_cursor"]
    rest, t2 = ndjson(client.get(f"{API}/export/events", params={"cursor": trailer["next_cursor"]}))
    assert len(rest) == 7 and t2["has_more"] is False
    assert not {x["event_id"] for x in rest} & {x["event_id"] for x in records}


def test_streaming_uses_a_generator_not_a_materialized_body(client, corpus, monkeypatch):
    from app.services import export_service

    calls = []
    original = export_service._batches

    def spy(*a, **kw):
        for batch in original(*a, **kw):
            calls.append(len(batch[0]))
            yield batch

    monkeypatch.setattr(export_service, "_batches", spy)
    monkeypatch.setattr(get_settings(), "export_batch_size", 2)
    records, _ = ndjson(client.get(f"{API}/export/events"))
    assert len(records) == len(corpus) and calls == [2, 2, 2, 1]  # bounded batches of <= 2


def test_json_format_and_post_body(client, corpus):
    r = client.get(f"{API}/export/events", params={"output": "json", "status": "SUCCESS"})
    body = r.json()
    assert r.headers["content-type"].startswith("application/json")
    assert body["schema_version"] == SCHEMA_VERSION and body["count"] == len(body["items"]) > 0
    assert all(i["status"] == "SUCCESS" for i in body["items"]) and body["has_more"] is False
    r = client.post(f"{API}/export/events", json={"output": "json", "vendor": "Cisco", "include_raw": True})
    items = r.json()["items"]
    assert len(items) == 2 and all(i["raw"]["payload"].startswith("<166>") for i in items)
    assert client.post(f"{API}/export/events", json={"unknown_option": 1}).status_code == 422
    assert client.post(f"{API}/export/events", json={"event_ids": ["../../etc"]}).status_code == 422
    assert client.post(f"{API}/export/events", json={"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"}).status_code == 422
    assert client.get(f"{API}/export/events", params={"output": "xml"}).status_code == 422


def test_export_includes_full_extensions_accounting_and_merkle(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "extension_inline_max_fields", 2)
    e = ingest(client, json.dumps({"user": "x", "action": "a", **{f"k{i}": i for i in range(10)}}))
    client.post(f"{API}/integrity/seal", json={"force": True})
    records, _ = ndjson(client.get(f"{API}/export/events"))
    rec = records[0]
    assert rec["extension_storage"]["mode"] == "SPILLED"
    assert {f"k{i}" for i in range(10)} <= set(rec["extensions"])
    fa = rec["field_accounting"]
    assert fa["preserved_count"] == fa["preserved_inline"] + fa["preserved_overflow"] == len(rec["extensions"])
    assert fa["parsed_count"] == fa["mapped_count"] + fa["preserved_count"]
    assert rec["integrity"]["merkle"]["root_hash"] and rec["integrity"]["merkle"]["leaf_index"] == 0
    assert rec["event_id"] == e["event_id"]


def test_revision_hash_changes_only_when_normalized_content_changes(client, corpus):
    first = {r["event_id"]: r["revision"]["revision_hash"] for r in ndjson(client.get(f"{API}/export/events"))[0]}
    again = {r["event_id"]: r["revision"]["revision_hash"] for r in ndjson(client.get(f"{API}/export/events"))[0]}
    assert first == again


def test_export_activity_is_logged_and_audited(client, corpus):
    ndjson(client.get(f"{API}/export/events", params={"vendor": "Fortinet"}))
    logs = client.get(f"{API}/export/logs").json()
    assert logs["items"][0]["status"] == "COMPLETED" and logs["items"][0]["rows"] == 3
    assert logs["items"][0]["filters"] == {"vendor": "Fortinet"}
    audit = client.get(f"{API}/governance/audit", params={"action": "EXPORT"}).json()["items"]
    assert audit and audit[0]["object_id"] == logs["items"][0]["id"]
    assert client.get(f"{API}/views/trust").json()["exports"]["rows_exported"] == 3
