"""Native PostgreSQL/NGINX 1/2/4-replica integrity and failure-injection check.

Uses unused local ports 8001..8004 and 8080, creates a uniquely named admin
account and test events, and stops only subprocesses started by this script.
Nothing is deleted. A local nginx executable and shared PostgreSQL are required.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

from benchmark import Client, sample_events, verify_events
from run_local import ROOT, load_environment
from router_report import summarize


def wait_for(predicate, description: str, timeout: float = 60):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as error:
            last_error = type(error).__name__
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {description}; last error={last_error}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        process.wait(timeout=55)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()


def free_ports(ports: list[int]) -> None:
    for port in ports:
        with socket.socket() as connection:
            try:
                connection.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"Port {port} is already in use. Stop its owner yourself before this test.") from None


def identifiers(response: dict) -> list[str]:
    return [item if isinstance(item, str) else item.get("id", item.get("event_id")) for item in response["events"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicas", type=int, nargs="+", default=[1, 2, 4], choices=[1, 2, 4])
    parser.add_argument("--output", type=Path, default=Path("artifacts/scale-verification.json"))
    args = parser.parse_args()
    load_environment()
    if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
        parser.error("Set DATABASE_URL to PostgreSQL. SQLite is a local development option, not a horizontal scaling test.")
    if not shutil.which(os.getenv("LOGFORGE_NGINX_BIN", "nginx")):
        parser.error("Install native NGINX and add it to PATH or set LOGFORGE_NGINX_BIN.")
    free_ports([8001, 8002, 8003, 8004, 8080, 9080])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    username = "scale-" + run_id
    password = secrets.token_urlsafe(24)
    bootstrap_env = {**os.environ, "LOGFORGE_BOOTSTRAP_PASSWORD": password}
    subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=ROOT / "backend", check=True)
    subprocess.run([sys.executable, "-m", "app.cli", "create-user", "--username", username, "--role", "admin", "--password-env", "LOGFORGE_BOOTSTRAP_PASSWORD"], cwd=ROOT / "backend", env=bootstrap_env, check=True)
    report = {"kind": "measured", "run_id": run_id, "database": "PostgreSQL", "replica_results": [], "passed": False}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        for count in args.replicas:
            processes: list[subprocess.Popen] = []
            files = []

            def launch(command: list[str], label: str, directory: Path, environment: dict | None = None):
                stream = (args.output.parent / f"scale-{run_id}-{count}-{label}.log").open("w", encoding="utf-8")
                files.append(stream)
                process = subprocess.Popen(command, cwd=directory, env=environment, stdout=stream, stderr=subprocess.STDOUT, creationflags=flags)
                processes.append(process)
                return process

            def backend(index: int):
                return launch([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(8001 + index), "--timeout-graceful-shutdown", "50"], f"backend-{index}", ROOT / "backend")

            result = {"replicas": count, "checks": {}, "passed": False}
            report["replica_results"].append(result)
            access_log = ROOT / "deploy/runtime/logs/access.log"
            access_offset = access_log.stat().st_size if access_log.exists() else 0
            try:
                replicas = [backend(index) for index in range(count)]
                for index in range(count):
                    wait_for(lambda i=index: Client(f"http://127.0.0.1:{8001+i}", timeout=2).request("/api/health/readiness"), f"backend {index} readiness")
                gateway_env = {**os.environ, "LOGFORGE_BACKEND_ADDRESSES": ",".join(f"127.0.0.1:{8001+i}" for i in range(count))}
                launch([sys.executable, str(ROOT / "deploy/health_router.py")], "gateway", ROOT, gateway_env)
                client = Client("http://127.0.0.1:8080")
                wait_for(lambda: client.request("/router/health").get("healthy") == count, "all replicas in healthy routing pool")
                client.login(username, password)
                marker = f"scale-{run_id}-{count}"
                events = sample_events(28, marker)
                payload = {"source": marker, "events": events, "idempotency_key": marker}
                with ThreadPoolExecutor(max_workers=count) as pool:
                    responses = list(pool.map(lambda _: client.request("/api/ingest", payload), range(count * 2)))
                assert all(identifiers(response) == identifiers(responses[0]) for response in responses), "Idempotency returned different authoritative events"
                result["checks"]["raw_sha_vault_normalization_lineage_events"] = verify_events(client, responses[0], events)
                result["checks"]["concurrent_idempotency"] = True
                result["checks"]["integrity_before_failure"] = client.request("/api/integrity")
                assert result["checks"]["integrity_before_failure"]["valid"] is True
                if count > 1:
                    progress = {"successes": 0, "transient_failures": 0, "verified": 0}

                    def traffic():
                        for sequence in range(20):
                            key = f"failure-{marker}-{sequence}"
                            submitted = sample_events(7, key)
                            body = {"source": marker, "events": submitted, "idempotency_key": key}
                            for attempt in range(10):
                                try:
                                    response = client.request("/api/ingest", body)
                                    progress["verified"] += verify_events(client, response, submitted)
                                    progress["successes"] += 1
                                    break
                                except RuntimeError:
                                    progress["transient_failures"] += 1
                                    if attempt == 9:
                                        raise
                                    time.sleep(0.25)
                            time.sleep(0.1)

                    with ThreadPoolExecutor(max_workers=1) as pool:
                        running = pool.submit(traffic)
                        time.sleep(0.3)
                        # Deliberately abrupt failure exercises transport error handling.
                        replicas[0].kill()
                        replicas[0].wait(timeout=5)
                        wait_for(lambda: client.request("/router/health").get("healthy") == count - 1, "failed replica removed")
                        assert any(process.poll() is None for process in replicas[1:])
                        replacement = backend(0)
                        replicas[0] = replacement
                        wait_for(lambda: client.request("/router/health").get("healthy") == count, "restarted replica reintroduced")
                        running.result()
                    result["checks"]["failure_recovery"] = progress
                else:
                    result["checks"]["failure_recovery"] = {"not_run": "Requires at least two replicas"}
                direct = Client("http://127.0.0.1:8001", client.token)
                direct.request("/api/admin/drain", {})
                if count > 1:
                    wait_for(lambda: client.request("/router/health").get("healthy") == count - 1, "draining replica removed")
                direct.request("/api/admin/undrain", {})
                wait_for(lambda: client.request("/router/health").get("healthy") == count, "undrained replica recovered")
                result["checks"]["drain_recovery"] = True
                result["checks"]["integrity_final"] = client.request("/api/integrity")
                assert result["checks"]["integrity_final"]["valid"] is True
                result["router_metrics"] = client.request("/router/metrics")
                result["distribution"] = summarize(access_log, access_offset)
                routed = result["distribution"]["upstream_requests"]
                assert all(any(f"127.0.0.1:{8001+i}" in address for address in routed) for i in range(count)), "Not every healthy replica received a request"
                result["passed"] = True
            except Exception as error:
                result["error"] = f"{type(error).__name__}: {error}"
                raise
            finally:
                for process in reversed(processes):
                    stop(process)
                for stream in files:
                    stream.close()
        report["passed"] = all(result["passed"] for result in report["replica_results"])
    finally:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Scale report: {args.output}; passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

