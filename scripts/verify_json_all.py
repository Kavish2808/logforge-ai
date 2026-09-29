import urllib.request
import json

BASE_URL = "http://127.0.0.1:8000"

def send(endpoint, payload):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

print("=== 1. Pretty-printed multi-line JSON ===")
pretty_json = """{
  "timestamp": "2026-09-30T03:30:00Z",
  "client_ip": "192.168.1.100",
  "method": "POST",
  "path": "/api/login",
  "status": 200,
  "message": "User admin login successful"
}"""
res1 = send("/api/v1/ingest", {"events": [{"raw": pretty_json}]})
print(f"Accepted: {res1.get('accepted')}, Total: {res1.get('total')}")
r1 = res1.get("results", [{}])[0]
print(f"Status: {r1.get('status')}, Format: {r1.get('format_detected')}, Adapter: {r1.get('adapter_id')}, OCSF: {r1.get('ocsf_class_name')}")
print(f"Action: {r1.get('event_action')}, Network: {r1.get('network')}")
assert r1.get("status") == "SUCCESS", "Pretty JSON failed!"
assert res1.get("accepted") == 1, "Pretty JSON was fragmented!"

print("\n=== 2. JSON Array in Console Event ===")
array_json = json.dumps([
    {"time": "2026-09-30T03:31:00Z", "src_ip": "10.0.0.1", "action": "allow", "message": "conn 1"},
    {"time": "2026-09-30T03:31:01Z", "src_ip": "10.0.0.2", "action": "deny", "message": "conn 2"}
])
res2 = send("/api/v1/ingest", {"events": [{"raw": array_json}]})
print(f"Accepted: {res2.get('accepted')}, Total: {res2.get('total')}")
for item in res2.get("results", []):
    print(f"  Status: {item.get('status')}, Format: {item.get('format_detected')}, IP: {item.get('network', {}).get('src_ip')}, Action: {item.get('event_action')}")
assert res2.get("accepted") == 2, "Array was not unrolled properly!"

print("\n=== 3. Root JSON Array Body ===")
root_array = [
    {"timestamp": "2026-09-30T03:32:00Z", "src_ip": "172.16.0.5", "message": "batch item 1"},
    {"timestamp": "2026-09-30T03:32:01Z", "src_ip": "172.16.0.6", "message": "batch item 2"}
]
res3 = send("/api/v1/ingest", root_array)
print(f"Accepted: {res3.get('accepted')}, Total: {res3.get('total')}")
assert res3.get("accepted") == 2

print("\n=== 4. NDJSON (Newline Delimited JSON) ===")
ndjson = '{"src_ip": "1.2.3.4", "message": "ndjson 1"}\n{"src_ip": "5.6.7.8", "message": "ndjson 2"}'
res4 = send("/api/v1/ingest", {"events": [{"raw": ndjson}]})
print(f"Accepted: {res4.get('accepted')}, Total: {res4.get('total')}")
assert res4.get("accepted") == 2

print("\n=== 5. Standard IngestRequest with Multi-line Pretty JSON ===")
res5 = send("/api/v1/ingest", {"raw_log": pretty_json, "source_hint": "json_generic"})
print(f"Status: {res5.get('status')}, Format: {res5.get('format_detected')}, Adapter: {res5.get('adapter_id')}")
assert res5.get("status") == "SUCCESS"

print("\n ALL JSON VERIFICATION TESTS PASSED SUCCESSFULLY! ")
