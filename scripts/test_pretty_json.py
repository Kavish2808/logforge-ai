import urllib.request
import json

BASE_URL = "http://127.0.0.1:8000"

pretty_json = """{
  "timestamp": "2026-09-30T03:00:00Z",
  "src_ip": "10.0.0.1",
  "dst_ip": "203.0.113.1",
  "action": "allow",
  "message": "User authorized"
}"""

print("--- Testing Pretty Printed Multi-line JSON Ingestion ---")
payload = {"source": "json-test", "events": [{"raw": pretty_json}]}
req = urllib.request.Request(
    f"{BASE_URL}/api/v1/ingest",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(f"Accepted: {data.get('accepted')}, Total: {data.get('total')}")
        for r in data.get("results", []):
            print(f"Status:   {r.get('status')}")
            print(f"Format:   {r.get('format_detected')}")
            print(f"Error:    {r.get('error_message')}")
            print(f"Raw:      {repr(r.get('raw_event'))[:60]}")
except Exception as e:
    print(f"ERROR: {e}")
