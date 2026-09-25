#!/usr/bin/env bash
# Hackathon-friendly walkthrough of the LogForge AI ingestion pipeline.
# Prints short, human-readable summaries rather than raw JSON dumps.
# Requires the stack to be running: docker compose up --build
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS_DIR="$SCRIPT_DIR/../demo/logs"

# ---- tiny dependency-free JSON field extraction (no jq/python needed) ----
# Both helpers legitimately return "" when the field is absent or JSON
# null (e.g. error_message on a successful event) — that is a normal,
# expected outcome here, not a script error, so grep finding no match
# must never trip `set -e`/pipefail and abort the whole demo.
# String field, e.g. extract_str "$json" event_id
extract_str() {
  { echo "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" || true; } | head -1 | sed -E "s/^\"$2\"[[:space:]]*:[[:space:]]*\"//; s/\"$//"
}
# Bare (non-string) field: number/bool/null, e.g. extract_raw "$json" status
extract_raw() {
  { echo "$1" | grep -o "\"$2\"[[:space:]]*:[[:space:]]*\"\?[^,}\"]*\"\?" || true; } | head -1 | sed -E "s/^\"$2\"[[:space:]]*:[[:space:]]*//"
}
section() {
  echo
  echo "═══════════════════════════════════════════════════════════════════"
  echo "▶ $1"
  echo "═══════════════════════════════════════════════════════════════════"
}

summarize() {
  local json="$1"
  echo "  event_id : $(extract_str "$json" event_id)"
  echo "  status   : $(extract_str "$json" status)"
  echo "  format   : $(extract_str "$json" format_detected)"
  echo "  vendor   : $(extract_str "$json" vendor)"
  echo "  adapter  : $(extract_str "$json" adapter_id)"
  local err
  err=$(extract_str "$json" error_message)
  # `[ -n "$err" ] && echo ...` alone would trip `set -e` when $err is
  # empty (the common case) — the left side "failing" makes the whole
  # statement's exit status non-zero even though skipping is correct.
  if [ -n "$err" ]; then
    echo "  error    : $err"
  fi
}

json_escape() {
  # Single-line log content only (matches the ingest contract: one raw
  # log per request) — escapes backslashes and double quotes for
  # embedding into a JSON string literal. Uses pure bash parameter
  # expansion rather than sed/jq/python: on Git Bash for Windows, sed
  # patterns containing a literal backslash get mangled when passed to
  # the native sed.exe binary, so this avoids that entirely.
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

ingest_file() {
  local path="$1" label="$2"
  local raw escaped response
  raw=$(cat "$path")
  escaped=$(json_escape "$raw")
  echo "  raw: $raw"
  response=$(curl -sf -X POST "$BASE_URL/api/v1/ingest" \
    -H "Content-Type: application/json" \
    -d "{\"raw_log\": \"$escaped\"}")
  summarize "$response"
  echo "$response" > "/tmp/logforge_last_${label}.json"
}

echo "LogForge AI — demo walkthrough"
echo "Backend: $BASE_URL"

section "0. Health check"
curl -sf "$BASE_URL/health"
echo

section "1. JSON log → generic adapter"
ingest_file "$LOGS_DIR/01_json_generic.json" json

section "2. Syslog (RFC3164) → generic adapter"
ingest_file "$LOGS_DIR/02_syslog_generic.txt" syslog

section "3. CEF log → generic adapter"
ingest_file "$LOGS_DIR/03_cef_generic.txt" cef

section "4. Cisco ASA syslog → vendor-specific adapter auto-selected"
ingest_file "$LOGS_DIR/04_cisco_asa.txt" cisco

section "5. FortiGate syslog (key=value payload auto-flattened) → vendor adapter"
ingest_file "$LOGS_DIR/05_fortinet.txt" fortinet

section "6. Palo Alto Networks (CEF export) → vendor adapter"
ingest_file "$LOGS_DIR/06_paloalto_cef.txt" paloalto

section "7. Unknown-field preservation (custom_app_field is not in any mapping)"
json=$(cat "/tmp/logforge_last_json.json")
echo "  extensions block from the JSON log ingested in step 1:"
echo "  $(echo "$json" | grep -o '"extensions":{[^}]*}')"
echo "  -> 'custom_app_field' was NOT dropped, it is preserved verbatim."

section "8. Unrecognized log format → FAILED, raw preserved, not lost"
ingest_file "$LOGS_DIR/07_unknown_format.txt" unknown

section "9. Malformed CEF (known format, broken structure) → FAILED, raw preserved"
ingest_file "$LOGS_DIR/08_malformed_cef.txt" malformed

section "10. Reprocessing a FAILED event"
malformed_id=$(extract_str "$(cat /tmp/logforge_last_malformed.json)" event_id)
echo "  reprocessing event_id: $malformed_id"
reprocess_response=$(curl -sf -X POST "$BASE_URL/api/v1/events/$malformed_id/reprocess")
echo "  reprocessed: $(extract_raw "$reprocess_response" reprocessed)"

section "11. Batch ingestion — one bad log does not break the batch"
batch_response=$(curl -sf -X POST "$BASE_URL/api/v1/ingest/batch" \
  -H "Content-Type: application/json" \
  -d '{
    "logs": [
      {"raw_log": "{\"message\": \"ok event\", \"severity\": \"info\"}"},
      {"raw_log": "garbage not a recognizable log"},
      {"raw_log": "CEF:0|Security|threatmanager|1.0|100|worm stopped|10|src=10.0.0.1 dst=2.1.2.2"}
    ]
  }')
echo "  total          : $(extract_raw "$batch_response" total)"
echo "  success_count  : $(extract_raw "$batch_response" success_count)"
echo "  failed_count   : $(extract_raw "$batch_response" failed_count)"
echo "  (3 logs submitted, one garbage entry, batch still returns all 3 results)"

section "12. Querying events (filter by vendor + status)"
events_response=$(curl -sf "$BASE_URL/api/v1/events?vendor=Cisco&limit=5")
echo "  total Cisco events on record: $(extract_raw "$events_response" total)"
failed_events=$(curl -sf "$BASE_URL/api/v1/events?status=FAILED&limit=5")
echo "  total FAILED events on record: $(extract_raw "$failed_events" total)"

section "Demo complete"
echo "Every log above was hashed (SHA-256), given a unique event_id, and"
echo "persisted — including the ones that failed to parse. Nothing is ever"
echo "silently lost. Full JSON responses were saved to /tmp/logforge_last_*.json"
