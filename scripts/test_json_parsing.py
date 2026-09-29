import urllib.request
import json

BASE_URL = "http://127.0.0.1:8000"

test_cases = [
    {
        "name": "Plain standard JSON log",
        "log": json.dumps({"timestamp": "2026-09-30T03:00:00Z", "src_ip": "10.20.0.12", "dst_ip": "203.0.113.24", "message": "User logged in", "severity": "info"})
    },
    {
        "name": "CloudTrail / AWS JSON log",
        "log": json.dumps({"eventTime": "2026-09-30T03:00:00Z", "eventName": "ConsoleLogin", "sourceIPAddress": "198.51.100.1", "userIdentity": {"userName": "admin"}})
    },
    {
        "name": "Syslog wrapped JSON log",
        "log": '<134>Sep 30 03:00:00 srv01 app: {"timestamp":"2026-09-30T03:00:00Z","src_ip":"10.0.1.1","action":"allow"}'
    },
    {
        "name": "JSON Array of objects",
        "log": '[{"user": "alice", "action": "login"}, {"user": "bob", "action": "logout"}]'
    }
]

for tc in test_cases:
    print(f"\n--- Testing: {tc['name']} ---")
    payload = {"source": "test-json", "events": [{"raw": tc["log"]}]}
    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            res = data.get("results", [{}])[0]
            print(f"Status:   {res.get('status')}")
            print(f"Format:   {res.get('format_detected')}")
            print(f"Adapter:  {res.get('adapter_id')}")
            print(f"OCSF Cls: {res.get('ocsf_class_name')}")
            print(f"Error:    {res.get('error_message')}")
            print(f"Warnings: {res.get('warnings')}")
    except Exception as e:
        print(f"HTTP ERROR: {e}")
