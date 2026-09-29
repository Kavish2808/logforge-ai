#!/usr/bin/env python3
"""LogForge AI — Multi-Device Syslog Forwarder / Ingestion Gateway.

Listens for incoming Syslog streams (UDP or TCP port 514/1514) from network
devices (Cisco, Fortinet, Palo Alto, Linux, routers, switches), buffers logs,
and flushes them in batches directly into LogForge AI's deterministic
pre-processing pipeline via POST /api/v1/ingest/batch.

Usage:
    python scripts/syslog_forwarder.py [--host 0.0.0.0] [--port 514] [--proto udp] [--target http://localhost:8000/api/v1/ingest/batch]
"""
import argparse
import json
import logging
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("syslog_forwarder")


class SyslogForwarder:
    def __init__(
        self,
        listen_host: str = "0.0.0.0",
        listen_port: int = 514,
        protocol: str = "udp",
        target_url: str = "http://localhost:8000/api/v1/ingest/batch",
        api_token: str | None = None,
        batch_size: int = 50,
        flush_interval_sec: float = 1.0,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.protocol = protocol.lower()
        self.target_url = target_url
        self.api_token = api_token
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

        self._buffer: deque[str] = deque()
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        # Background worker for batch flushing
        flusher = threading.Thread(target=self._flush_loop, daemon=True)
        flusher.start()

        if self.protocol == "tcp":
            self._listen_tcp()
        else:
            self._listen_udp()

    def stop(self):
        self._running = False
        self._flush_now()

    def _enqueue(self, line: str):
        cleaned = line.strip()
        if not cleaned:
            return
        with self._lock:
            self._buffer.append(cleaned)
            should_flush = len(self._buffer) >= self.batch_size
        if should_flush:
            self._flush_now()

    def _flush_loop(self):
        while self._running:
            time.sleep(self.flush_interval_sec)
            self._flush_now()

    def _flush_now(self):
        batch: list[str] = []
        with self._lock:
            while self._buffer and len(batch) < self.batch_size:
                batch.append(self._buffer.popleft())

        if not batch:
            return

        payload = {"logs": [{"raw_log": log} for log in batch]}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        req = urllib.request.Request(self.target_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status_code = resp.getcode()
                resp_data = json.loads(resp.read().decode("utf-8"))
                logger.info(
                    "Forwarded %d logs -> HTTP %d (Success: %d, Under Review: %d, Failed: %d)",
                    len(batch),
                    status_code,
                    resp_data.get("success_count", 0),
                    resp_data.get("under_review_count", 0),
                    resp_data.get("failed_count", 0),
                )
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            logger.error("Failed to forward batch to LogForge AI (HTTP %d): %s", exc.code, err_body)
        except Exception as exc:
            logger.error("Connection error forwarding batch to LogForge AI: %s", exc)

    def _listen_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.listen_host, self.listen_port))
        except PermissionError:
            logger.error(
                "Permission denied binding to port %d. Try running with admin/sudo or use a port >= 1024 (e.g. 1514).",
                self.listen_port,
            )
            sys.exit(1)

        logger.info(
            "Syslog UDP listener active on %s:%d -> Forwarding to %s",
            self.listen_host,
            self.listen_port,
            self.target_url,
        )

        while self._running:
            try:
                data, addr = sock.recvfrom(65535)
                text = data.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    self._enqueue(line)
            except Exception as exc:
                if self._running:
                    logger.warning("Error receiving UDP packet: %s", exc)

    def _listen_tcp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.listen_host, self.listen_port))
        except PermissionError:
            logger.error(
                "Permission denied binding to port %d. Try running with admin/sudo or use a port >= 1024 (e.g. 1514).",
                self.listen_port,
            )
            sys.exit(1)

        sock.listen(10)
        logger.info(
            "Syslog TCP listener active on %s:%d -> Forwarding to %s",
            self.listen_host,
            self.listen_port,
            self.target_url,
        )

        while self._running:
            try:
                client_sock, client_addr = sock.accept()
                threading.Thread(target=self._handle_tcp_client, args=(client_sock, client_addr), daemon=True).start()
            except Exception as exc:
                if self._running:
                    logger.warning("TCP accept error: %s", exc)

    def _handle_tcp_client(self, client_sock: socket.socket, addr):
        with client_sock:
            buffer = ""
            while self._running:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                lines = buffer.split("\n")
                for line in lines[:-1]:
                    self._enqueue(line)
                buffer = lines[-1]
            if buffer.strip():
                self._enqueue(buffer.strip())


def main():
    parser = argparse.ArgumentParser(description="LogForge AI Multi-Device Syslog Forwarder")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host interface (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=514, help="Listen port (default: 514, or 1514 if non-root)")
    parser.add_argument("--proto", choices=["udp", "tcp"], default="udp", help="Protocol: udp or tcp (default: udp)")
    parser.add_argument(
        "--target",
        default="http://localhost:8000/api/v1/ingest/batch",
        help="LogForge AI batch ingestion URL (default: http://localhost:8000/api/v1/ingest/batch)",
    )
    parser.add_argument("--token", default=None, help="Bearer token for LogForge AI (optional if RBAC_MODE=permissive)")
    parser.add_argument("--batch-size", type=int, default=50, help="Max batch size before immediate flush (default: 50)")
    parser.add_argument("--interval", type=float, default=1.0, help="Flush interval in seconds (default: 1.0)")

    args = parser.parse_args()

    forwarder = SyslogForwarder(
        listen_host=args.host,
        listen_port=args.port,
        protocol=args.proto,
        target_url=args.target,
        api_token=args.token,
        batch_size=args.batch_size,
        flush_interval_sec=args.interval,
    )

    try:
        forwarder.start()
    except KeyboardInterrupt:
        logger.info("Shutting down syslog forwarder...")
        forwarder.stop()


if __name__ == "__main__":
    main()
