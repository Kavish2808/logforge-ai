"""Integration registry contract (E2E defect D6): routes used to be unauthenticated and
`deliver` answered DELIVERED without sending anything. Now: SOC_ADMIN only, audited, and a
delivery request is truthfully refused (501 NOT_IMPLEMENTED) - verified against a local receiver."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.api.routes import integrations
from tests.integration.phase7.conftest import API

PUBLIC = "http://93.184.216.34/hook"  # public literal IP: passes the destination policy without DNS


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(integrations, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(integrations, "_DATA_PATH", str(tmp_path / "integrations.json"))
    return tmp_path


def audit(client, headers, action):
    return client.get(f"{API}/governance/audit", params={"action": action}, headers=headers).json()["items"]


@pytest.mark.parametrize("method,path", [("get", "/integrations"), ("post", "/integrations"),
                                         ("post", "/integrations/int_x/deliver")])
@pytest.mark.parametrize("prefix", ["", API])
def test_1_unauthenticated_access_is_refused(client, method, path, prefix):
    kwargs = {"json": {"name": "x", "url": PUBLIC}} if method == "post" else {}
    r = getattr(client, method)(prefix + path, **kwargs)
    assert r.status_code == 401, r.text


def test_1b_non_admin_roles_are_forbidden(client, users):
    for who in ("analyst", "eng1"):
        assert client.get(f"{API}/integrations", headers=users[who]).status_code == 403
        assert client.post(f"{API}/integrations", json={"name": "x", "url": PUBLIC}, headers=users[who]).status_code == 403


def test_2_3_admin_registers_a_configured_integration_and_it_is_audited(client, users, store):
    r = client.post(f"{API}/integrations", json={"name": "siem", "url": PUBLIC}, headers=users["admin"])
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["status"] == "CONFIGURED" and item["delivery"] == "NOT_IMPLEMENTED" and item["created_by"] == "admin"
    assert [i["id"] for i in client.get(f"{API}/integrations", headers=users["admin"]).json()] == [item["id"]]
    [entry] = audit(client, users["admin"], "INTEGRATION_CREATE")
    assert entry["decision"] == "SUCCESS" and entry["actor"] == "admin" and entry["object_id"] == item["id"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.5/x", "http://169.254.169.254/latest",
                                 "file:///etc/passwd", "ftp://93.184.216.34/x", "http:///nohost"])
def test_destination_policy_rejects_unsafe_targets_and_audits_the_denial(client, users, url):
    r = client.post(f"{API}/integrations", json={"name": "bad", "url": url}, headers=users["admin"])
    assert r.status_code == 422 and "Traceback" not in r.text
    assert audit(client, users["admin"], "INTEGRATION_CREATE")[0]["decision"] == "DENIED"


def test_4_unknown_integration_is_404(client, users):
    assert client.post(f"{API}/integrations/int_nope/deliver", headers=users["admin"]).status_code == 404


def test_5_6_8_10_11_delivery_is_truthfully_refused_and_nothing_reaches_a_receiver(client, users, monkeypatch):
    received: list[str] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(integrations, "validate_destination", lambda url: None)  # allow the local receiver
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        iid = client.post(f"{API}/integrations", json={"name": "local", "url": url}, headers=users["admin"]).json()["id"]
        for _ in range(2):  # a duplicate request behaves identically
            r = client.post(f"{API}/integrations/{iid}/deliver", headers=users["admin"])
            assert r.status_code == 501, r.text
            body = r.json()
            assert body["error"]["code"] == "NOT_IMPLEMENTED" and "nothing was sent" in body["error"]["message"]
            assert "DELIVERED" not in r.text
        assert received == []
        entries = audit(client, users["admin"], "INTEGRATION_DELIVERY")
        assert len(entries) == 2 and all(e["decision"] == "FAILED" and e["object_id"] == iid for e in entries)
        assert entries[0]["details"]["delivery"] == "NOT_IMPLEMENTED"
        assert client.get(f"{API}/integrations", headers=users["admin"]).json()[0]["status"] == "CONFIGURED"
    finally:
        server.shutdown()


def test_12_delivery_request_does_not_touch_the_alert_bus(client, users):
    iid = client.post(f"{API}/integrations", json={"name": "siem", "url": PUBLIC}, headers=users["admin"]).json()["id"]
    before = client.get(f"{API}/alerts/counts").json()
    assert client.post(f"{API}/integrations/{iid}/deliver", headers=users["admin"]).status_code == 501
    assert client.get(f"{API}/alerts/counts").json() == before
