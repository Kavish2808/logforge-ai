"""Measure the real HTTP ingestion path; write measured values without projections."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid


class Client:
    def __init__(self, base_url: str, token: str = "", timeout: float = 180):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.last_replica: str | None = None

    def request(self, path: str, payload: object = None, method: str | None = None) -> object:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.last_replica = response.headers.get("X-LogForge-Replica")
                body = response.read().decode()
                return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body
        except urllib.error.HTTPError as error:
            # No raw event bodies or credentials are written to benchmark reports.
            raise RuntimeError(f"HTTP {error.code} for {path}") from None

    def login(self, username: str, password: str) -> None:
        result = self.request("/api/auth/login", {"username": username, "password": password})
        self.token = result["access_token"]


def sample_events(count: int, marker: str, extra_bytes: int = 0) -> list[dict]:
    events = []
    for index in range(count):
        identity = f"{marker}-{index}"
        samples = [
            f"<134>Sep 28 12:00:00 router %ASA-6-302013: Built outbound TCP connection {index} for outside:10.0.0.1/443 to inside:192.168.1.5/50000 marker={identity}",
            f"CEF:0|Acme|Firewall|1|100|Network connection|5|src=192.0.2.10 dst=198.51.100.20 spt=443 dpt=8443 act=allow msg={identity}",
            f"LEEF:2.0|Acme|Firewall|1|100|^|src=192.0.2.10^dst=198.51.100.20^action=allow^marker={identity}",
            f'<event><src_ip>192.0.2.10</src_ip><dst_ip>198.51.100.20</dst_ip><action>allow</action><marker>{identity}</marker></event>',
            json.dumps({"vendor": "generic", "src_ip": "192.0.2.10", "dst_ip": "198.51.100.20", "action": "allow", "marker": identity, "extra": {f"field_{n}": n for n in range(40)}}),
            f'{{"malformed": "unterminated, marker={identity}',
            json.dumps({"vendor": "fortinet", "srcip": "192.0.2.10", "dstip": "198.51.100.20", "action": "deny", "marker": identity, "payload": "x" * extra_bytes}),
        ]
        events.append({"raw": samples[index % len(samples)]})
    return events


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_report(values: list[float]) -> dict:
    return {"samples": len(values), "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99), "mean_ms": statistics.mean(values) if values else None,
            "note": "Request latency, not inferred per-event latency. Low sample counts are descriptive only."}


class ResourceSampler:
    def __init__(self, pids: list[int]):
        self.stop = threading.Event()
        self.samples: list[dict] = []
        self.processes = []
        self.observed = {}
        self.reason = "No --server-pid supplied; server CPU/RSS unavailable."
        if pids:
            try:
                import psutil
                self.processes = [psutil.Process(pid) for pid in pids]
                for process in self._tree():
                    process.cpu_percent()
                    self.observed[process.pid] = process
                self.reason = "Supplied server PIDs and descendants (including Windows venv launcher children). CPU can exceed 100%; summed RSS may count shared pages more than once."
            except Exception as error:
                self.reason = f"Resource sampling unavailable: {type(error).__name__}"

    def _tree(self) -> list:
        processes = {}
        for root in self.processes:
            processes[root.pid] = root
            for process in root.children(recursive=True):
                processes[process.pid] = self.observed.get(process.pid, process)
        return list(processes.values())

    def run(self) -> None:
        while not self.stop.wait(0.5):
            if not self.processes:
                continue
            try:
                processes = self._tree()
                self.samples.append({"cpu_percent": sum(p.cpu_percent() for p in processes),
                                     "rss_bytes": sum(p.memory_info().rss for p in processes)})
                self.observed.update({process.pid: process for process in processes})
            except Exception:
                self.reason = "One or more sampled processes exited; partial sampling retained."

    def report(self) -> dict:
        return {"note": self.reason, "samples": len(self.samples),
                "observed_pids": sorted(self.observed),
                "cpu_percent_mean": statistics.mean(s["cpu_percent"] for s in self.samples) if self.samples else None,
                "cpu_percent_max": max((s["cpu_percent"] for s in self.samples), default=None),
                "rss_bytes_max": max((s["rss_bytes"] for s in self.samples), default=None)}


def database_snapshot() -> dict:
    url = os.getenv("LOGFORGE_BENCHMARK_DATABASE_URL", "")
    if not url:
        return {"available": False, "reason": "Set LOGFORGE_BENCHMARK_DATABASE_URL for optional PostgreSQL statistics."}
    try:
        import psycopg
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5) as connection:
            row = connection.execute("SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, tup_inserted, tup_updated, temp_bytes, deadlocks, pg_database_size(datname) FROM pg_stat_database WHERE datname = current_database()").fetchone()
            keys = ["connections", "commits", "rollbacks", "blocks_read", "blocks_hit", "rows_inserted", "rows_updated", "temp_bytes", "deadlocks", "database_bytes"]
            return {"available": True, **dict(zip(keys, row))}
    except Exception as error:
        return {"available": False, "reason": type(error).__name__}


def metric_values(text: str) -> dict[str, float]:
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        try:
            values[key] = float(value)
        except ValueError:
            pass
    return values


def api_resource_report(before: str, after: str, same_replica: bool) -> dict:
    initial, final = metric_values(before), metric_values(after)
    cpu_key, memory_key = "logforge_process_cpu_seconds_total", "logforge_process_resident_memory_bytes"
    delta = final[cpu_key] - initial[cpu_key] if same_replica and cpu_key in initial and cpu_key in final else None
    return {"same_replica": same_replica, "cpu_seconds_before": initial.get(cpu_key),
        "cpu_seconds_after": final.get(cpu_key), "cpu_seconds_delta": delta if delta is None or delta >= 0 else None,
        "rss_bytes_before": initial.get(memory_key), "rss_bytes_after": final.get(memory_key),
        "note": "Backend self-reported endpoint samples, not peak RSS; CPU delta requires the same replica before/after. Includes integrity verification work."}


def verify_events(client: Client, response: dict, expected: list[dict]) -> int:
    identifiers = response.get("events", [])
    if len(identifiers) != len(expected):
        raise AssertionError("Ingest response does not account for every submitted event")
    for item, event in zip(identifiers, expected):
        event_id = item if isinstance(item, str) else item.get("id", item.get("event_id"))
        stored = client.request(f"/api/events/{event_id}")
        raw = base64.b64decode(stored["raw_base64"], validate=True)
        assert raw == event["raw"].encode("utf-8"), "Raw vault bytes differ"
        digest = hashlib.sha256(raw).hexdigest()
        assert stored.get("raw_sha256", stored.get("sha256")) == digest, "SHA-256 mismatch"
        assert stored.get("integrity") is True, "Event integrity check failed"
        assert stored.get("normalized") is not None, "Normalized event missing"
        assert stored.get("lineage") is not None, "Lineage missing"
        assert stored.get("accounting", {}).get("lost_count") == 0, "Field accounting reports evidence loss"
        revisions = stored.get("revisions", [])
        assert revisions and all(revision["raw_sha256"] == digest for revision in revisions), "Revision evidence mismatch"
        assert revisions[0]["kind"] == "ORIGINAL", "Original revision was not retained"
        proofs = stored.get("merkle", [])
        assert proofs and all(proof["valid"] is True for proof in proofs), "Merkle proof verification failed"
    return len(expected)


def run(args: argparse.Namespace) -> dict:
    client = Client(args.url, os.getenv(args.token_env, ""))
    if not client.token:
        password = os.getenv(args.password_env)
        if not password:
            raise RuntimeError(f"Set {args.password_env} or {args.token_env}; secrets are never command arguments.")
        client.login(args.username, password)
    run_id = uuid.uuid4().hex
    report = {"report_version": 1, "kind": "measured", "run_id": run_id, "base_url": args.url,
              "source": args.source, "started_unix": time.time(), "batch_sizes": [], "database_before": database_snapshot(),
              "formats": ["syslog", "CEF", "LEEF", "XML", "extension-heavy JSON", "malformed", "JSON"],
              "extra_payload_bytes": args.extra_bytes, "concurrency": args.concurrency}
    sampler = ResourceSampler(args.server_pid)
    thread = threading.Thread(target=sampler.run, daemon=True)
    thread.start()
    try:
        report["metrics_before"] = client.request("/api/metrics")
        report["replica_before"] = client.last_replica
        for size in args.batch_sizes:
            latencies, accepted, errors = [], 0, []
            started = time.perf_counter()
            for repeat in range(args.repeats):
                marker = f"bench-{run_id}-{size}-{repeat}"
                events = sample_events(size, marker, args.extra_bytes)
                begin = time.perf_counter()
                try:
                    result = client.request("/api/ingest", {"source": args.source, "events": events, "idempotency_key": marker})
                    accepted += result["accepted"]
                    if result["accepted"] != size:
                        raise AssertionError("Fresh workload was not fully accepted")
                except (RuntimeError, AssertionError) as error:
                    errors.append(str(error))
                latencies.append((time.perf_counter() - begin) * 1000)
            elapsed = time.perf_counter() - started
            report["batch_sizes"].append({"events_per_request": size, "requests": args.repeats, "accepted": accepted,
                                          "elapsed_seconds": elapsed, "events_per_second": accepted / elapsed,
                                          "latency": latency_report(latencies), "errors": errors})
        deadline = time.monotonic() + args.sustained_seconds
        sustained_start = time.perf_counter()

        def sustained(worker: int) -> dict:
            latencies, accepted, errors, sequence = [], 0, [], 0
            while time.monotonic() < deadline:
                marker = f"sustained-{run_id}-{worker}-{sequence}"
                sequence += 1
                start = time.perf_counter()
                try:
                    result = client.request("/api/ingest", {"source": args.source,
                        "events": sample_events(args.sustained_batch, marker, args.extra_bytes), "idempotency_key": marker})
                    accepted += result["accepted"]
                except RuntimeError as error:
                    errors.append(str(error))
                    time.sleep(0.1)
                latencies.append((time.perf_counter() - start) * 1000)
            return {"accepted": accepted, "latencies": latencies, "errors": errors}

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(sustained, range(args.concurrency)))
        elapsed = time.perf_counter() - sustained_start
        report["sustained"] = {"duration_seconds": elapsed, "accepted": sum(item["accepted"] for item in results),
            "latency": latency_report([value for item in results for value in item["latencies"]]),
            "errors": [error for item in results for error in item["errors"]]}
        report["sustained"]["events_per_second"] = report["sustained"]["accepted"] / max(elapsed, 0.001)
        report["integrity"] = client.request("/api/integrity")
        report["metrics_after"] = client.request("/api/metrics")
        report["replica_after"] = client.last_replica
        report["api_process_resources"] = api_resource_report(report["metrics_before"], report["metrics_after"], bool(report["replica_before"]) and report["replica_before"] == report["replica_after"])
        report["database_after"] = database_snapshot()
    finally:
        sampler.stop.set()
        thread.join(timeout=2)
        report["resources"] = sampler.report()
    report["finished_unix"] = time.time()
    report["passed"] = report["integrity"].get("valid") is True and not report["sustained"]["errors"] and not any(item["errors"] for item in report["batch_sizes"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-env", default="LOGFORGE_BENCHMARK_PASSWORD")
    parser.add_argument("--token-env", default="LOGFORGE_TOKEN")
    parser.add_argument("--source", default="benchmark")
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sustained-seconds", type=float, default=30)
    parser.add_argument("--sustained-batch", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--extra-bytes", type=int, default=1024)
    parser.add_argument("--server-pid", type=int, action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark.json"))
    args = parser.parse_args()
    if any(size < 1 or size > 10000 for size in args.batch_sizes) or args.repeats < 1 or args.concurrency < 1 or args.sustained_seconds < 0 or not 1 <= args.sustained_batch <= 10000:
        parser.error("Positive sizes/repeats/concurrency required; batch size must be <=10000.")
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved measured benchmark to {args.output}; passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
