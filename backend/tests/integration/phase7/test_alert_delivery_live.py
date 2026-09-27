"""Alert delivery adapters exercised over real sockets (no mocks): a local HTTP
server stands in for webhook / Slack / Teams endpoints and a minimal local SMTP
server for email. This proves the wire behavior of the adapters; it does not
prove compatibility with the hosted Slack/Teams services themselves."""
import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import get_settings
from app.services import alert_service
from tests.conftest import TestSessionLocal
from tests.integration.phase7.conftest import API


class _Recorder(BaseHTTPRequestHandler):
    received: list = []
    status = 200
    delay = 0.0

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.delay:
            time.sleep(self.delay)
        type(self).received.append({"path": self.path, "body": json.loads(body),
                                    "content_type": self.headers.get("Content-Type")})
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_server():
    handler = type("Handler", (_Recorder,), {"received": [], "status": 200, "delay": 0.0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class _Smtp(socketserver.StreamRequestHandler):
    messages: list = []

    def _say(self, line: str):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._say("220 localhost test SMTP")
        mail = {"rcpt": []}
        while True:
            line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
            if not line:
                return
            cmd = line.upper()
            if cmd.startswith(("EHLO", "HELO")):
                self._say("250 localhost")
            elif cmd.startswith("MAIL FROM"):
                mail["from"] = line.split(":", 1)[1].strip()
                self._say("250 OK")
            elif cmd.startswith("RCPT TO"):
                mail["rcpt"].append(line.split(":", 1)[1].strip())
                self._say("250 OK")
            elif cmd == "DATA":
                self._say("354 End data with <CR><LF>.<CR><LF>")
                data = []
                while True:
                    d = self.rfile.readline().decode(errors="replace")
                    if d in (".\r\n", ".\n", ""):
                        break
                    data.append(d)
                mail["data"] = "".join(data)
                type(self).messages.append(dict(mail))
                self._say("250 OK queued")
            elif cmd == "QUIT":
                self._say("221 Bye")
                return
            else:
                self._say("250 OK")


@pytest.fixture()
def smtp_server():
    handler = type("SmtpHandler", (_Smtp,), {"messages": []})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield handler, server.server_address[1]
    server.shutdown()
    server.server_close()


def publish(kind="INTEGRITY_FAILURE", key="live-1"):
    with TestSessionLocal() as db:
        a = alert_service.publish(db, alert_service.AlertEvent(
            kind=kind, severity="CRITICAL", title="Chain broken", message="ROOT_MISMATCH at batch 7", dedup_key=key))
        return {d["channel"]: d for d in a.deliveries}, a.id


def test_webhook_slack_and_teams_are_delivered_over_http(client, http_server, monkeypatch):
    handler, base = http_server
    s = get_settings()
    monkeypatch.setattr(s, "alert_webhook_url", f"{base}/hook")
    monkeypatch.setattr(s, "alert_slack_webhook_url", f"{base}/slack")
    monkeypatch.setattr(s, "alert_teams_webhook_url", f"{base}/teams")
    deliveries, alert_id = publish()
    assert {k: v["ok"] for k, v in deliveries.items()} == {"internal": True, "webhook": True, "slack": True, "teams": True}
    by_path = {r["path"]: r for r in handler.received}
    assert set(by_path) == {"/hook", "/slack", "/teams"}
    assert all(r["content_type"] == "application/json" for r in handler.received)
    hook = by_path["/hook"]["body"]
    assert hook["id"] == alert_id and hook["kind"] == "INTEGRITY_FAILURE" and hook["severity"] == "CRITICAL"
    assert by_path["/slack"]["body"] == {"text": "[CRITICAL] Chain broken\nROOT_MISMATCH at batch 7"}
    assert by_path["/teams"]["body"]["title"] == "[CRITICAL] Chain broken"
    # The alert as stored names channels, never destination URLs.
    stored = client.get(f"{API}/alerts").json()["items"][0]
    assert base not in json.dumps(stored)


def test_http_error_and_timeout_are_recorded_not_raised(client, http_server, monkeypatch):
    handler, base = http_server
    s = get_settings()
    monkeypatch.setattr(s, "alert_webhook_url", f"{base}/hook")
    handler.status = 503
    deliveries, _ = publish(key="err")
    assert deliveries["webhook"]["ok"] is False and deliveries["webhook"]["error"] == "HTTPError"
    handler.status, handler.delay = 200, 1.5
    monkeypatch.setattr(s, "alert_delivery_timeout_seconds", 0.3)
    started = time.perf_counter()
    deliveries, _ = publish(key="slow")
    assert deliveries["webhook"]["ok"] is False and time.perf_counter() - started < 1.4  # bounded by the timeout
    assert deliveries["internal"]["ok"] is True  # internal delivery never depends on external channels


def test_unreachable_destination_is_recorded(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "http://127.0.0.1:9/nothing-listens-here")
    deliveries, _ = publish(key="down")
    assert deliveries["webhook"]["ok"] is False


def test_non_http_scheme_is_refused(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "file:///etc/passwd")
    deliveries, _ = publish(key="scheme")
    assert deliveries["webhook"] == {**deliveries["webhook"], "ok": False, "error": "ValueError"}


def test_email_is_delivered_over_smtp(client, smtp_server, monkeypatch):
    handler, port = smtp_server
    s = get_settings()
    for name, value in (("alert_smtp_host", "127.0.0.1"), ("alert_smtp_port", port),
                        ("alert_email_from", "logforge@example.test"), ("alert_email_to", "soc@example.test, lead@example.test")):
        monkeypatch.setattr(s, name, value)
    deliveries, _ = publish(key="mail")
    assert deliveries["email"]["ok"] is True
    [msg] = handler.messages
    assert msg["from"] == "<logforge@example.test>" and msg["rcpt"] == ["<soc@example.test>", "<lead@example.test>"]
    assert "Subject: [LogForge CRITICAL] Chain broken" in msg["data"] and "ROOT_MISMATCH at batch 7" in msg["data"]
