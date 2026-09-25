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

## 11. Drift detection (Phase 5)

Drift is evaluated only for vendor-specific adapters (`cisco_asa`, `fortinet`,
`paloalto_cef`). The first event from a source auto-bootstraps its provisional
baseline; step 5 above already did that for `paloalto_cef`.

Inspect the baseline (`origin: "auto_bootstrap"`):

```bash
curl -s http://localhost:8000/api/v1/drift/baselines | jq
curl -s http://localhost:8000/api/v1/drift/baselines/paloalto_cef | jq
```

Ingest a Palo Alto event whose structure changed (different extension keys).
It is persisted in full with `status: "UNDER_REVIEW"` and a drift record:

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "CEF:0|Palo Alto Networks|PAN-OS|11.0.0|traffic|THREAT|5|rt=1705650300000 src=10.0.0.30 dst=93.184.216.34 spt=51500 dpt=443 proto=tcp act=allow deviceExternalId=0123456789 cs2Label=Zone cs2=trust cn1=42"}' \
  | jq '{event_id, status, drift: .processing_metadata.drift}'
```

Read the deterministic drift report (change types, severity, critical fields,
recommendations) and its human-readable explanation:

```bash
curl -s "http://localhost:8000/api/v1/events/$DRIFT_EVENT_ID" \
  | jq '.processing_metadata.drift | {status, severity, change_types, critical_field_changes, recommended_actions}'
curl -s "http://localhost:8000/api/v1/events/$DRIFT_EVENT_ID" | jq -r '.processing_metadata.drift.explanation'
```

Remove a critical field (the source IP, `src`) from the step-5 structure —
similarity stays high, but the event is still sent to review as a
critical-field change:

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|THREAT|5|dst=93.184.216.34 spt=51500 dpt=443 proto=tcp act=allow suser=jdoe"}' \
  | jq '.processing_metadata.drift | {status, similarity, decision_reasons, critical_field_changes, severity}'
```

Possible format drift — a FortiGate log whose tag changed case no longer
matches the `fortinet` adapter and falls back to `syslog_generic`; once
`fortinet` is a known source (ingest step 4 first), it is flagged:

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"raw_log": "<189>Jan 18 12:00:00 FGT100E FortiGate: date=2026-01-18 time=12:00:00 devname=\"FGT100E\" srcip=10.0.0.15 srcport=51422 dstip=8.8.8.8 dstport=53 proto=17 action=\"accept\" msg=\"DNS query\""}' \
  | jq '{adapter_id, status, drift: (.processing_metadata.drift | {status, source_key, current_adapter, evidence, severity})}'
```

List the review queue:

```bash
curl -s "http://localhost:8000/api/v1/events?status=UNDER_REVIEW" | jq '.items[] | {event_id, adapter_id, drift: .processing_metadata.drift.status, severity: .processing_metadata.drift.severity}'
```

Human review — accept the new structure as an additional variant, or make it
the new baseline (`"mode": "replace_baseline"`):

```bash
DRIFT_EVENT_ID="<event_id of the UNDER_REVIEW event>"
curl -s -X POST "http://localhost:8000/api/v1/events/$DRIFT_EVENT_ID/drift/accept" \
  -H "Content-Type: application/json" \
  -d '{"mode": "add_variant", "note": "PAN-OS 11 upgrade, adapter reviewed"}' | jq
```

For a `POSSIBLE_FORMAT_DRIFT` event (or a drift you want to close without
changing the baseline) use `{"mode": "acknowledge"}`.

See how the source's structure evolved (v1 created → v2 variant → v3 replaced):

```bash
curl -s http://localhost:8000/api/v1/drift/baselines/paloalto_cef | jq '.history[] | {version, action, event_id, changes}'
```

Accepting an event that is not under drift review returns `409`:

```bash
# {"error": {"code": "CONFLICT", "message": "Event '...' is not UNDER_REVIEW due to structural drift."}}
```

## 12. Onboarding an unknown source (Phase 3)

An unknown key=value source is `FAILED` today (raw preserved):

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest -H "Content-Type: application/json" \
  -d '{"raw_log": "vendor=ACMEFW ts=2026-01-18T12:00:00Z srcip=10.0.0.1 dstip=8.8.8.8 srcport=40000 dstport=443 action=allow sev=high"}' \
  | jq '{status, format_detected}'
```

Start a session with samples (10–15 recommended; `event_ids` of FAILED
events work too):

```bash
SAMPLES=$(for i in $(seq 1 12); do printf '"vendor=ACMEFW ts=2026-01-18T12:%02d:00Z srcip=10.0.0.%d dstip=8.8.8.8 srcport=%d dstport=443 action=allow sev=high",' $i $i $((40000+i)); done)
SESSION=$(curl -s -X POST http://localhost:8000/api/v1/onboarding/sessions -H "Content-Type: application/json" \
  -d "{\"samples\": [${SAMPLES%,}]}" | jq -r .id)
```

Get a suggestion (Claude if `ANTHROPIC_API_KEY` is set, else the offline
analyzer) — it is validated in the sandbox immediately, but NOT activated:

```bash
curl -s -X POST "http://localhost:8000/api/v1/onboarding/sessions/$SESSION/suggest" \
  -H "Content-Type: application/json" -d '{"provider": "auto"}' \
  | jq '{status, result: .validation.result, metrics: (.validation.metrics | {matched_samples, total_samples, match_rate, mapping_coverage}), activation}'
curl -s "http://localhost:8000/api/v1/onboarding/sessions/$SESSION" | jq -r .explanation
```

Approve the exact proposal version (human decision), then future logs are
parsed by the approved adapter without any LLM call:

```bash
curl -s -X POST "http://localhost:8000/api/v1/onboarding/sessions/$SESSION/approve" \
  -H "Content-Type: application/json" -d '{"proposal_version": 1, "approved_by": "analyst", "note": "reviewed"}' \
  | jq '.adapter | {adapter_id, version, status}'

curl -s -X POST http://localhost:8000/api/v1/ingest -H "Content-Type: application/json" \
  -d '{"raw_log": "vendor=ACMEFW ts=2026-01-19T08:00:00Z srcip=10.9.9.9 dstip=1.1.1.1 srcport=50000 dstport=53 action=deny sev=high"}' \
  | jq '{status, adapter_id, adapter_version, source: .processing_metadata.adapter_source, network}'
```

Or reject (`POST .../reject {"reason": "..."}`), edit the proposal
(`PUT .../proposal {"proposal": {...}}`), inspect versions
(`GET /api/v1/onboarding/adapters/acmefw_acmefw`) and roll back
(`POST /api/v1/onboarding/adapters/acmefw_acmefw/rollback`).

## 13. Error responses (consistent envelope)

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
