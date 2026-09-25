# LogForge AI — Manual demo (curl)

Assumes the stack is running (`docker compose up --build`) and the backend is reachable
at `http://localhost:8000`.

## 1. Health check

```bash
curl -s http://localhost:8000/health
```

## 2. Ingest a JSON log

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "{\"timestamp\": \"2026-01-18T12:00:00Z\", \"message\": \"User login successful\", \"severity\": \"info\", \"user\": \"jdoe\", \"src_ip\": \"10.0.0.5\"}"}' | jq
```

## 3. Ingest a Cisco ASA syslog log (vendor mapping auto-selected)

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "<166>Jan 18 12:05:00 ciscoasa %ASA-6-302013: Built outbound TCP connection 123456789 for outside:203.0.113.10/443 to inside:10.0.0.20/52345"}' | jq
```

## 4. Ingest a FortiGate syslog log

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "<189>Jan 18 12:00:00 FGT100E FORTIGATE: date=2026-01-18 time=12:00:00 devname=\"FGT100E\" srcip=10.0.0.15 srcport=51422 dstip=8.8.8.8 dstport=53 proto=17 action=\"accept\" msg=\"DNS query\""}' | jq
```

## 5. Ingest a Palo Alto Networks log (CEF format)

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|5|src=10.0.0.30 dst=93.184.216.34 spt=51500 dpt=443 proto=tcp act=allow suser=jdoe"}' | jq
```

## 6. Ingest an unrecognizable log (shows FAILED status, raw preserved)

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "this is not syslog, json, or cef"}' | jq
```

## 7. Batch ingestion (mixed valid/invalid — one bad log does not break the batch)

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{
    "logs": [
      {"raw_log": "{\"message\": \"ok event\", \"severity\": \"info\"}"},
      {"raw_log": "garbage not a log"},
      {"raw_log": "CEF:0|Security|threatmanager|1.0|100|worm stopped|10|src=10.0.0.1 dst=2.1.2.2"}
    ]
  }' | jq
```

## 8. List events (filter by status, vendor, format, or adapter)

```bash
curl -s "http://localhost:8000/api/v1/events?status=FAILED&limit=10" | jq
curl -s "http://localhost:8000/api/v1/events?vendor=Fortinet&limit=10" | jq
curl -s "http://localhost:8000/api/v1/events?adapter_id=cisco_asa&limit=10" | jq
```

## 9. Get a single event by ID

```bash
EVENT_ID="<paste an event_id from a previous response>"
curl -s "http://localhost:8000/api/v1/events/$EVENT_ID" | jq
```

## 10. Reprocess an event

```bash
curl -s -X POST "http://localhost:8000/api/v1/events/$EVENT_ID/reprocess" | jq
```

## 11. Error responses (consistent envelope)

A non-existent event:

```bash
curl -s http://localhost:8000/api/v1/events/01ARZ3NDEKTSV4RRFFQ69G5FAV | jq
# {"error": {"code": "NOT_FOUND", "message": "Event '...' not found"}}
```

An invalid request (empty `raw_log`, or a `raw_log` over 256 KB, or a batch
over 1000 items) returns `422` with field-level detail:

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" -d '{"raw_log": ""}' | jq
# {"error": {"code": "VALIDATION_ERROR", "message": "...", "fields": {"raw_log": "..."}}}
```
