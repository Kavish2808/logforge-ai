"""Walkthrough: Online Web Device Registration and Direct Live Log Streaming to LogForge AI."""
import json
import urllib.request
import time

BASE_URL = "http://127.0.0.1:8000"

def run_walkthrough():
    print("=" * 70)
    print("STEP 1: Register an Online Web Device in LogForge AI")
    print("=" * 70)

    device_payload = {
        "name": "cloud-web-cluster-01",
        "device_type": "Web Application / Cloud Edge",
        "vendor": "Nginx Web Proxy",
        "protocol": "HTTP REST Webhook",
        "format": "JSON Access Log",
        "description": "Production customer-facing Nginx ingress cluster routing web traffic"
    }

    req = urllib.request.Request(
        f"{BASE_URL}/api/devices",
        data=json.dumps(device_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            device = json.loads(resp.read().decode("utf-8"))
            print(f" [+] Web Device Successfully Registered!")
            print(f"     * Device ID:    {device['id']}")
            print(f"     * Device Name:  {device['name']}")
            print(f"     * Secret Token: {device['token']}")
            print(f"     * Status:       {device['status']}")
            device_id = device["id"]
            token = device["token"]
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f" [*] Device already exists, retrieving existing registration...")
            with urllib.request.urlopen(f"{BASE_URL}/api/devices") as resp:
                devices = json.loads(resp.read().decode("utf-8"))
                device = next(d for d in devices if d["name"] == "cloud-web-cluster-01")
                device_id = device["id"]
                token = device["token"]
                print(f"     * Device ID:    {device_id}")
                print(f"     * Secret Token: {token}")
        else:
            raise

    print("\n" + "=" * 70)
    print("STEP 2: The Online Web Device Dumps Live Access & Security Logs")
    print("=" * 70)
    
    # 3 Realistic online web requests (Access log, Auth attempt, Potential attack probe)
    live_web_logs = [
        json.dumps({
            "timestamp": "2026-09-29T23:58:01Z",
            "host": "api.logforge.enterprise.com",
            "client_ip": "198.51.100.88",
            "method": "GET",
            "path": "/api/v1/checkout",
            "status": 200,
            "bytes_sent": 4120,
            "response_time_ms": 34,
            "user_agent": "Mozilla/5.0 Chrome/120.0"
        }),
        json.dumps({
            "timestamp": "2026-09-29T23:58:02Z",
            "host": "auth.logforge.enterprise.com",
            "client_ip": "203.0.113.45",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "status": 401,
            "bytes_sent": 284,
            "response_time_ms": 120,
            "user_agent": "curl/7.88.1"
        }),
        json.dumps({
            "timestamp": "2026-09-29T23:58:03Z",
            "host": "app.logforge.enterprise.com",
            "client_ip": "192.0.2.14",
            "method": "GET",
            "path": "/admin/sql-console?query=SELECT+*+FROM+users",
            "status": 403,
            "bytes_sent": 142,
            "response_time_ms": 12,
            "user_agent": "SqlMap/1.6"
        })
    ]

    dump_payload = {
        "token": token,
        "logs": live_web_logs
    }

    dump_url = f"{BASE_URL}/api/devices/{device_id}/logs"
    print(f" [>] Online Web Device is sending {len(live_web_logs)} logs to:")
    print(f"     POST {dump_url}")
    
    dump_req = urllib.request.Request(
        dump_url,
        data=json.dumps(dump_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Device-Token": token
        }
    )

    t0 = time.time()
    with urllib.request.urlopen(dump_req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        elapsed_ms = (time.time() - t0) * 1000

    print(f"\n [OK] LogForge Ingestion Pipeline Result ({elapsed_ms:.1f} ms):")
    print(f"     * Device ID:       {result.get('device_id')}")
    print(f"     * Total Received:  {result.get('total')}")
    print(f"     * Accepted Logs:   {result.get('accepted')}")
    print(f"     * OCSF Successes:  {result.get('success_count')}")

    print("\n" + "=" * 70)
    print("STEP 3: Cryptographic Proof & OCSF Normalization Verification")
    print("=" * 70)
    for idx, r in enumerate(result.get("results", []), 1):
        print(f" Event #{idx}:")
        print(f"   - ULID Event ID:   {r.get('event_id')}")
        print(f"   - Detected Format: {r.get('format_detected')}")
        print(f"   - OCSF Status:     {r.get('status')}")
        print(f"   - SHA-256 Hash:    {r.get('raw_hash')}")

    print("\n" + "=" * 70)
    print("STEP 4: Check Device Status in Central Inventory")
    print("=" * 70)
    with urllib.request.urlopen(f"{BASE_URL}/api/devices/{device_id}") as resp:
        updated_dev = json.loads(resp.read().decode("utf-8"))
        print(f" * Current Device Status: {updated_dev.get('status')}")
        print(f" * Total Device Events:   {updated_dev.get('event_count')}")
        print(f" * Last Seen Timestamp:   {updated_dev.get('last_seen')}")
        print(f" * Recent Logs Cached:    {len(updated_dev.get('recent_logs', []))} entries")

    print("\n" + "=" * 70)
    print("WALKTHROUGH COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_walkthrough()
