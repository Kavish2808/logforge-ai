import urllib.request
import json

base = "http://127.0.0.1:8000"

endpoints = [
    ("GET", "/health"),
    ("GET", "/health/readiness"),
    ("GET", "/api/health/readiness"),
    ("GET", "/dashboard"),
    ("GET", "/api/dashboard"),
    ("GET", "/sources"),
    ("GET", "/api/sources"),
    ("GET", "/baselines"),
    ("GET", "/api/baselines"),
    ("GET", "/drift"),
    ("GET", "/api/drift"),
    ("GET", "/drift/statistical"),
    ("GET", "/api/drift/statistical"),
    ("GET", "/correlations"),
    ("GET", "/api/correlations"),
    ("GET", "/adapters"),
    ("GET", "/api/adapters"),
    ("GET", "/replays"),
    ("GET", "/api/replays"),
    ("GET", "/integrity"),
    ("GET", "/api/integrity"),
    ("GET", "/audit"),
    ("GET", "/api/audit"),
    ("GET", "/reviews"),
    ("GET", "/api/reviews"),
    ("GET", "/api/alerts"),
    ("GET", "/events?limit=5"),
    ("GET", "/api/events?limit=5"),
    ("GET", "/integrations"),
    ("GET", "/api/integrations"),
]

print("Testing console endpoints:")
passed = 0
failed = 0

for method, path in endpoints:
    url = f"{base}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  [OK] {method:4} {path:30} -> {resp.status}")
            passed += 1
    except Exception as e:
        print(f"  [FAIL] {method:4} {path:30} -> {e}")
        failed += 1

print(f"\nResult: PASSED={passed}, FAILED={failed}")
