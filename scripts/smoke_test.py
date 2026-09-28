"""Run a real native HTTP/API smoke and benchmark against an isolated SQLite DB.

Creates temporary credentials in memory, starts only its own API process, writes
non-secret JSON reports, then stops the process and removes its temporary DB.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from benchmark import Client, run, sample_events, verify_events
from run_local import ROOT
from verify_scale import wait_for


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 100, 1000, 10000])
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as connection:
        try:
            connection.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"Port {args.port} is in use; choose a free port.")
    password = secrets.token_urlsafe(24)
    username = "smoke-" + uuid.uuid4().hex[:10]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    summary = {"database": "isolated SQLite", "real_http": True, "passed": False}
    with tempfile.TemporaryDirectory(prefix="logforge-smoke-", dir=output) as temporary:
        assert Path(temporary).resolve().is_relative_to(output), "Temporary directory escaped output root"
        environment = {**os.environ, "DATABASE_URL": "sqlite:///" + (Path(temporary) / "smoke.db").as_posix(),
            "LOGFORGE_ENV": "development", "LOGFORGE_SECRET_KEY": secrets.token_hex(32), "LOGFORGE_BOOTSTRAP_PASSWORD": password}
        subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=ROOT / "backend", env=environment, check=True)
        subprocess.run([sys.executable, "-m", "app.cli", "create-user", "--username", username, "--role", "admin", "--password-env", "LOGFORGE_BOOTSTRAP_PASSWORD"], cwd=ROOT / "backend", env=environment, check=True)
        with (output / "smoke-server.log").open("w", encoding="utf-8") as server_log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port), "--timeout-graceful-shutdown", "20"],
                cwd=ROOT / "backend", env=environment, stdout=server_log, stderr=subprocess.STDOUT, creationflags=flags)
            try:
                client = Client(f"http://127.0.0.1:{args.port}")
                wait_for(lambda: client.request("/api/health/readiness"), "smoke API readiness")
                client.login(username, password)
                marker = "smoke-" + uuid.uuid4().hex
                events = sample_events(28, marker)
                payload = {"source": "smoke", "events": events, "idempotency_key": marker}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(lambda _: client.request("/api/ingest", payload), range(8)))
                assert sum(item["accepted"] for item in responses) == 28, "Concurrent idempotency produced duplicate accepted events"
                summary["concurrent_requests"] = 8
                summary["verified_evidence_events"] = verify_events(client, responses[0], events)
                for response in responses:
                    assert [event["id"] for event in response["events"]] == [event["id"] for event in responses[0]["events"]]
                summary["integrity_before_benchmark"] = client.request("/api/integrity")
                os.environ["LOGFORGE_SMOKE_TOKEN"] = client.token
                benchmark_args = argparse.Namespace(url=client.base_url, token_env="LOGFORGE_SMOKE_TOKEN", password_env="UNUSED", username=username,
                    source="smoke-benchmark", batch_sizes=args.batch_sizes, repeats=1, sustained_seconds=2,
                    sustained_batch=10, concurrency=1, extra_bytes=1024, server_pid=[process.pid])
                measured = run(benchmark_args)
                (output / "benchmark-smoke.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
                assert measured["passed"], "Benchmark or integrity gate failed"
                client.request("/api/admin/drain", {})
                try:
                    client.request("/api/health/readiness")
                    raise AssertionError("Draining process remained ready")
                except RuntimeError as error:
                    assert "HTTP 503" in str(error)
                client.request("/api/admin/undrain", {})
                client.request("/api/health/readiness")
                summary["drain_undrain"] = True
                summary["passed"] = True
            finally:
                os.environ.pop("LOGFORGE_SMOKE_TOKEN", None)
                if process.poll() is None:
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                        process.wait(timeout=25)
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                        process.wait()
                (output / "smoke-verification.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Native HTTP smoke passed={summary['passed']}; reports: {output}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
