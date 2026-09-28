"""MODE B — HTTP/API benchmark: a real `uvicorn app.main:app` process (one
worker, no --reload, access log off) bound to 127.0.0.1 and the benchmark
database, driven over keep-alive HTTP/1.1 connections by client threads.

The client runs in the same container as the server, so the two compete for
the same CPUs; the report states this. Feature variants are selected with the
application's own environment switches (DRIFT_ENABLED, RAW_VAULT_ENABLED,
PHASE8_ENABLED).
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from logforge_bench.environment import BACKEND_DIR
from logforge_bench.inprocess import VARIANTS

INGEST = "/api/v1/ingest"
INGEST_BATCH = "/api/v1/ingest/batch"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, *, bench_url: str, workdir: Path, variant: str, scheduler: bool,
                 scheduler_interval: int = 60, port: int | None = None):
        spec = VARIANTS[variant]
        if not spec["persist"]:
            raise SystemExit(f"variant '{variant}' has no HTTP equivalent (it does not persist)")
        self.port = port or _free_port()
        self.variant = variant
        self.log_path = workdir / f"uvicorn-{variant}-{self.port}.log"
        self.env = {**os.environ, "DATABASE_URL": bench_url, "DRIFT_ENABLED": str(spec["drift"]).lower(),
                    "RAW_VAULT_ENABLED": str(spec["vault"]).lower(), "PHASE8_ENABLED": str(spec["phase8"]).lower(),
                    "SCHEDULER_ENABLED": str(scheduler).lower(), "SCHEDULER_INTERVAL_SECONDS": str(scheduler_interval),
                    "RAW_VAULT_PATH": str(workdir / "raw_vault"), "EVIDENCE_ANCHOR_PATH": str(workdir / "anchors"),
                    "DEBUG": "false"}
        self.cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port),
                    "--workers", "1", "--no-access-log", "--log-level", "warning"]
        self.config = {"command": " ".join(self.cmd[1:]), "variant": variant, "scheduler_enabled": scheduler,
                       "scheduler_interval_s": scheduler_interval, "uvicorn_workers": 1, "reload": False,
                       "access_log": False,
                       "note": "the dev compose stack runs 'uvicorn --reload' with access logging; this benchmark "
                               "server does not (a reload watcher is not part of a serving deployment)"}
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Server":
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(self.cmd, cwd=BACKEND_DIR, env=self.env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"benchmark server exited early; see {self.log_path}")
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.3)
        raise SystemExit("benchmark server did not become healthy within 60 s")

    @property
    def pid(self) -> int:
        return self.proc.pid

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


class ExternalTarget:
    """Step 10B: an already-running HTTP endpoint (the nginx load balancer in front of N replicas).
    The harness starts nothing; replica processes are observed from the host (`docker stats`)."""

    def __init__(self, url: str, *, replicas: int | None, variant: str, scheduler: bool):
        from urllib.parse import urlparse

        u = urlparse(url)
        self.host, self.port = u.hostname, u.port or 80
        self.pid = None
        self.log_path = None
        self.config = {"target_url": url, "replicas": replicas, "variant": variant, "scheduler_enabled": scheduler,
                       "note": "external target: nginx round-robin -> N identical uvicorn replicas "
                               "(1 worker each, no --reload, access log off) -> the shared PostgreSQL"}

    def __enter__(self) -> "ExternalTarget":
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                c = http.client.HTTPConnection(self.host, self.port, timeout=3)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    return self
            except OSError:
                pass
            time.sleep(0.5)
        raise SystemExit(f"target {self.host}:{self.port} did not answer /health within 60 s")

    def __exit__(self, *exc) -> None:
        return None


def run(server: Server, events: list, *, workers: int = 1, batch_size: int = 1, timeout_s: float = 30.0,
        on_record: Callable[[dict[str, Any]], None] | None = None, stop_at: float | None = None,
        event_source: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Send events (a list, or `event_source()` until `stop_at`) and record
    one record per event plus one per request."""
    from logforge_bench.inprocess import _event_record

    records: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    lock = threading.Lock()
    it = iter([events[i:i + batch_size] for i in range(0, len(events), batch_size)]) if events else None
    host = getattr(server, "host", "127.0.0.1")

    def next_chunk():
        with lock:
            if stop_at is not None and time.perf_counter() >= stop_at:
                return None
            if it is not None:
                return next(it, None)
            return [event_source() for _ in range(batch_size)]

    def worker():
        conn = http.client.HTTPConnection(host, server.port, timeout=timeout_s)
        while True:
            chunk = next_chunk()
            if chunk is None:
                break
            if len(chunk) == 1 and batch_size == 1:
                path, body = INGEST, {"raw_log": chunk[0].raw}
            else:
                path, body = INGEST_BATCH, {"logs": [{"raw_log": e.raw} for e in chunk]}
            payload = json.dumps(body).encode()
            t = time.perf_counter()
            status, data, err, upstream = None, b"", None, None
            try:
                conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                status, data, upstream = resp.status, resp.read(), resp.getheader("X-Upstream")
            except socket.timeout:
                err = "TIMEOUT"
            except (OSError, http.client.HTTPException) as exc:
                err = f"{type(exc).__name__}: {str(exc)[:120]}"
            elapsed = (time.perf_counter() - t) * 1000
            if err:
                conn.close()
                conn = http.client.HTTPConnection(host, server.port, timeout=timeout_s)
            results: list[dict[str, Any] | None] = [None] * len(chunk)
            if status in (200, 201):
                parsed = json.loads(data)
                results = [parsed] if path == INGEST else parsed["results"]
            elif status is not None:
                err = f"HTTP {status}"
            req = {"t_end": time.perf_counter(), "latency_ms": round(elapsed, 3), "http_status": status or err,
                   "events": len(chunk), "request_bytes": len(payload), "response_bytes": len(data),
                   "upstream": upstream, "epoch_end": round(time.time(), 3)}
            recs = []
            for ev, res in zip(chunk, results):
                rec = _event_record(ev, res, elapsed / len(chunk), err)
                rec["request_latency_ms"] = round(elapsed, 3)
                rec["t_end"] = req["t_end"]
                rec["upstream"] = upstream
                rec["event_id"] = (res or {}).get("event_id")
                recs.append(rec)
            with lock:
                requests.append(req)
                records.extend(recs)
            if on_record:
                for rec in recs:
                    on_record(rec)
        conn.close()

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, name=f"bench-http-{i}") for i in range(workers)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wall = time.perf_counter() - t0
    for r in requests:
        r["t_end"] = round(r["t_end"] - t0, 4)
    for r in records:
        r["t_end"] = round(r["t_end"] - t0, 4)
    records.sort(key=lambda r: r["i"])
    return {"records": records, "requests": requests, "wall_s": wall}


def request_summary(requests: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter

    from logforge_bench.metrics import latency_summary

    statuses = Counter(str(r["http_status"]) for r in requests)
    return {"requests": len(requests), "http_status_distribution": dict(statuses),
            "timeouts": statuses.get("TIMEOUT", 0),
            "transport_or_http_errors": sum(v for k, v in statuses.items() if k not in ("200", "201")),
            "request_latency": latency_summary([r["latency_ms"] for r in requests]),
            "response_bytes_mean": round(sum(r["response_bytes"] for r in requests) / len(requests), 1) if requests else None,
            "request_bytes_mean": round(sum(r["request_bytes"] for r in requests) / len(requests), 1) if requests else None}
