"""Active readiness discovery for NGINX OSS; never examines event payloads.

The controller owns the NGINX master and its generated upstream include. A
failed probe removes an address on the next successful reload. Between probes,
NGINX's passive checks cover transport errors; clients must retain idempotency
keys because no health check eliminates the failure-after-check race.
"""
from __future__ import annotations

import concurrent.futures
import http.server
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import urllib.request

PREFIX = Path(os.getenv("LOGFORGE_NGINX_PREFIX", str(Path(__file__).parent / "runtime"))).resolve()
CONFIG = PREFIX / "upstreams.conf"
NGINX = os.getenv("LOGFORGE_NGINX_BIN", "nginx")
HOST = os.getenv("LOGFORGE_BACKEND_HOST", "backend")
PORT = int(os.getenv("LOGFORGE_BACKEND_PORT", "8000"))
STATIC = os.getenv("LOGFORGE_BACKEND_ADDRESSES", "127.0.0.1:8001,127.0.0.1:8002")
INTERVAL = max(0.5, float(os.getenv("LOGFORGE_HEALTH_INTERVAL", "2")))
STOP = threading.Event()
LOCK = threading.Lock()
STATE = {"ready": False, "discovered": 0, "healthy": 0, "reloads": 0,
         "probe_failures": 0, "reload_failures": 0, "dns_failures": 0,
         "last_probe_unix": 0.0}


def upstream_config(addresses: list[str], port: int = PORT) -> str:
    """Validate addresses so discovered DNS strings cannot become config code."""
    if not 1 <= port <= 65535:
        raise ValueError("backend port must be in 1..65535")
    safe = set()
    for address in addresses:
        hostname, separator, explicit_port = address.partition(":")
        address_port = int(explicit_port) if separator else port
        if not 1 <= address_port <= 65535:
            raise ValueError("backend port must be in 1..65535")
        safe.add(f"{ipaddress.IPv4Address(hostname)}:{address_port}")
    servers = [f"    server {address} max_fails=1 fail_timeout=3s;" for address in sorted(safe)]
    if not servers:
        servers = ["    server 127.0.0.1:65535 down;"]
    return "upstream logforge_backends {\n" + "\n".join(servers) + "\n    keepalive 32;\n}\n"


def discover() -> list[str]:
    if STATIC:
        values = [item.strip() for item in STATIC.split(",") if item.strip()]
        upstream_config(values)  # Validate before probes or NGINX interpolation.
        return sorted(set(values))
    try:
        return sorted({f"{item[4][0]}:{PORT}" for item in socket.getaddrinfo(HOST, PORT, socket.AF_INET, socket.SOCK_STREAM)})
    except OSError:
        with LOCK:
            STATE["dns_failures"] += 1
        return []


def probe(address: str) -> bool:
    try:
        request = urllib.request.Request(f"http://{address}/api/health/readiness")
        # Ignore proxy environment variables for private health probes.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1.5) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def atomic_write(content: str) -> None:
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, CONFIG)


def install(addresses: list[str], master: subprocess.Popen | None) -> bool:
    previous = CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else upstream_config([])
    atomic_write(upstream_config(addresses))
    check = subprocess.run(nginx_command("-t"), capture_output=True, text=True, timeout=10)
    if check.returncode:
        atomic_write(previous)
        print(json.dumps({"component": "health-router", "error": "nginx config validation failed", "detail": check.stderr}), flush=True)
        with LOCK:
            STATE["reload_failures"] += 1
        return False
    if master is not None:
        try:
            subprocess.run(nginx_command("-s", "reload"), capture_output=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            atomic_write(previous)
            return False
        with LOCK:
            STATE["reloads"] += 1
    return True


def nginx_command(*args: str) -> list[str]:
    return [NGINX, "-p", PREFIX.as_posix() + "/", "-c", "nginx.conf", *args]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        with LOCK:
            state = dict(STATE)
        if self.path == "/health":
            fresh = time.time() - state["last_probe_unix"] < max(10, INTERVAL * 4)
            code = 200 if state["ready"] and fresh and not STOP.is_set() else 503
            body = json.dumps({**state, "fresh": fresh}).encode()
            content_type = "application/json"
        elif self.path == "/metrics":
            code, content_type = 200, "text/plain; version=0.0.4"
            body = "".join(f"logforge_router_{key} {int(value) if isinstance(value, bool) else value}\n" for key, value in state.items()).encode()
        else:
            code, content_type, body = 404, "application/json", b'{"detail":"Not found"}'
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def main() -> int:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    (PREFIX / "logs").mkdir(exist_ok=True)
    template = (Path(__file__).parent / "nginx.conf").read_text(encoding="utf-8")
    frontend = (Path(__file__).resolve().parents[1] / "frontend" / "dist").as_posix()
    (PREFIX / "nginx.conf").write_text(template.replace("__FRONTEND_DIST__", frontend), encoding="utf-8")
    atomic_write(upstream_config([]))
    if not install([], None):
        return 1
    master = subprocess.Popen(nginx_command("-g", "daemon off;"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 9080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: STOP.set())
    current: list[str] = []
    try:
        while not STOP.is_set() and master.poll() is None:
            addresses = discover()
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(probe, addresses))
            healthy = [address for address, ready in zip(addresses, results) if ready]
            if healthy != current and install(healthy, master):
                current = healthy
                print(json.dumps({"component": "health-router", "healthy_replicas": len(current)}), flush=True)
            with LOCK:
                STATE.update(discovered=len(addresses), healthy=len(current), ready=bool(current), last_probe_unix=time.time())
                STATE["probe_failures"] += sum(not ready for ready in results)
            STOP.wait(INTERVAL)
    finally:
        STOP.set()
        server.shutdown()
        if master.poll() is None:
            subprocess.run(nginx_command("-s", "quit"), capture_output=True, timeout=10)
            try:
                master.wait(timeout=55)
            except subprocess.TimeoutExpired:
                master.terminate()
                master.wait(timeout=3)
    return master.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())

