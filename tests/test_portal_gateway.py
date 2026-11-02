"""Gateway transport tests use isolated data and never touch a production workbook."""
import base64
from contextlib import contextmanager
from email.message import Message
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import make_handler
from sop_gateway import GatewayConfig, GatewayError, canonical_origin
from sop_store import ValidationError


KEY = "x" * 48
PRINCIPAL = "EXAMPLE\\alice"
ORIGIN = "https://data.example.test"


class StoreStub:
    def __init__(self):
        self.saved = []

    def save_plans(self, body):
        self.saved.append(body)
        return {"ok": True}

    def list_products(self):
        return {"items": []}


class RefreshStub:
    def status(self):
        return {"status": "watching"}


class RoundtripStub:
    def __init__(self):
        self.actors = []

    def import_content(self, content, choice=None, *, actor=None):
        self.actors.append(actor)
        return {"ok": True}


@contextmanager
def running(*, writes=False, gateway_mode=True):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "index.html").write_text("<h1>isolated SOP</h1>", encoding="utf-8")
        store, roundtrip = StoreStub(), RoundtripStub()
        server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        cfg = GatewayConfig("/sop", ORIGIN, KEY, writes) if gateway_mode else None
        server.RequestHandlerClass = make_handler(store, root, server.server_port,
            source_refresh=RefreshStub(), plan_roundtrip=roundtrip, gateway=cfg)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        try:
            yield server, store, roundtrip
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def request(server, path="/sop/", *, method="GET", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    request_headers = {"X-SOP-Proxy-Key": KEY, "X-SOP-User": PRINCIPAL}
    if method == "POST":
        request_headers.update({"Content-Type": "application/json", "Origin": ORIGIN})
    if headers:
        for key, value in headers.items():
            if value is None:
                request_headers.pop(key, None)
            else:
                request_headers[key] = value
    connection.request(method, path, json.dumps(body if body is not None else {}) if method == "POST" else None,
                       request_headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


class GatewayHTTPTest(unittest.TestCase):
    def test_rejected_post_drains_a_delayed_small_body_before_returning_403(self):
        with running(writes=True) as (server, store, _):
            # Send the headers ahead of the body: rejecting/closing immediately
            # used to race these unread bytes and reset the response on Windows.
            body = json.dumps({"owner": "forged", "notes": "x" * 4096}).encode()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.putrequest("POST", "/sop/api/plans")
            for name, value in {"X-SOP-Proxy-Key": KEY, "X-SOP-User": PRINCIPAL,
                                "Origin": "https://untrusted.example", "Content-Type": "application/json",
                                "Content-Length": str(len(body))}.items():
                connection.putheader(name, value)
            connection.endheaders()
            time.sleep(.05)
            connection.send(body)
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertIn("error", json.loads(response.read()))
            connection.close()
            self.assertEqual(store.saved, [])

    def test_rejected_post_does_not_wait_indefinitely_for_an_absent_body(self):
        with running(writes=False) as (server, store, _):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.putrequest("POST", "/sop/api/plans")
            for name, value in {"X-SOP-Proxy-Key": KEY, "X-SOP-User": PRINCIPAL, "Origin": ORIGIN,
                                "Content-Type": "application/json", "Content-Length": "64"}.items():
                connection.putheader(name, value)
            start = time.monotonic()
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertIn("error", json.loads(response.read()))
            self.assertLess(time.monotonic() - start, 2)
            connection.close()
            self.assertEqual(store.saved, [])

    def test_prefixed_page_can_embed_and_root_cannot_bypass_prefix(self):
        with running() as (server, store, _):
            code, headers, body = request(server)
            self.assertEqual(code, 200)
            self.assertIn(b"isolated SOP", body)
            self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
            self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'self'")
            self.assertEqual(request(server, "/api/products")[0], 404)
            self.assertEqual(request(server, "/sop-other/api/products")[0], 404)
            self.assertEqual(request(server, "/sop")[0:1], (308,))
            self.assertEqual(request(server, "/sop")[1]["Location"], "/sop/")
            code, headers, body = request(server, method="HEAD")
            self.assertEqual(code, 200)
            self.assertEqual(body, b"")

    def test_secret_and_valid_windows_identity_are_mandatory(self):
        with running() as (server, _, _):
            for headers in ({"X-SOP-Proxy-Key": None}, {"X-SOP-Proxy-Key": "forged"},
                            {"X-SOP-User": None}, {"X-SOP-User": "self-claimed"}, {"X-SOP-User": "\\"},
                            {"Host": "external.example"}):
                with self.subTest(headers=headers):
                    self.assertEqual(request(server, headers=headers)[0], 403)
            self.assertEqual(request(server, headers={"Host": "data.example.test"})[0], 200)

    def test_bridge_base64_identity_preserves_unicode_without_allowing_controls(self):
        with running(writes=True) as (server, store, _):
            principal = "EXAMPLE\\王小明"
            encoded = "b64:" + base64.b64encode(principal.encode("utf-8")).decode("ascii")
            code, _, body = request(server, "/sop/api/session", headers={"X-SOP-User": encoded})
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["user"], principal)
            request(server, "/sop/api/plans", method="POST", body={"changes": []}, headers={"X-SOP-User": encoded})
            self.assertEqual(store.saved[0]["owner"], principal)
            for invalid in ("b64:%%", "b64:/w==", "b64:" + base64.b64encode(b"DOMAIN\\a\n").decode("ascii")):
                self.assertEqual(request(server, headers={"X-SOP-User": invalid})[0], 403)

    def test_origin_is_explicit_and_writes_fail_closed(self):
        with running() as (server, store, _):
            code, _, body = request(server, "/sop/api/session")
            self.assertEqual(json.loads(body), {"mode": "gateway", "user": PRINCIPAL, "canWrite": False})
            self.assertEqual(request(server, "/sop/api/write-access", method="POST")[0], 403)
            self.assertEqual(request(server, "/sop/api/plans", method="POST", body={"owner": "forged"})[0], 403)
            self.assertEqual(request(server, headers={"Origin": "https://other.example"})[0], 403)
            self.assertEqual(store.saved, [])
        with running(writes=True) as (server, store, _):
            for origin in (None, "null", "http://data.example.test", ORIGIN + "/", ORIGIN + "@evil.example"):
                self.assertEqual(request(server, "/sop/api/plans", method="POST", headers={"Origin": origin})[0], 403)
            self.assertEqual(request(server, "/sop/api/write-access", method="POST")[0], 200)
            self.assertEqual(store.saved, [])

    def test_owner_is_server_attributed_and_audited(self):
        with running(writes=True) as (server, store, _):
            payload = {"owner": "forged", "changes": [{"owner": "forged too", "sop": 7}]}
            with self.assertLogs("sop", level="INFO") as messages:
                code, _, _ = request(server, "/sop/api/plans", method="POST", body=payload)
            self.assertEqual(code, 200)
            self.assertEqual(store.saved, [{"owner": PRINCIPAL, "changes": [{"owner": PRINCIPAL, "sop": 7}]}])
            audit = next(line for line in messages.output if "sop_audit" in line)
            self.assertIn("alice", audit)
            self.assertIn('"status": 200', audit)
            self.assertNotIn(KEY, audit)
            self.assertNotIn("forged", audit)

    def test_import_receives_trusted_actor_and_server_open_is_disabled(self):
        with running(writes=True) as (server, _, roundtrip):
            payload = {"filename": "plan.xlsx", "content_base64": base64.b64encode(b"fixture").decode()}
            self.assertEqual(request(server, "/sop/api/plans/import-xlsx", method="POST", body=payload)[0], 200)
            self.assertEqual(roundtrip.actors, [PRINCIPAL])
            self.assertEqual(request(server, "/sop/api/plan-workbook/open", method="POST")[0], 403)

    def test_import_actor_reaches_committed_plan_audit_rows(self):
        from tests.test_roundtrip import RoundtripTest
        fixture = RoundtripTest()
        fixture.setUp()
        try:
            fixture.save("a", "2026-10", 4)
            fixture.export()
            fixture.change("a", "2026-10", 9)
            result = fixture.service.import_content(fixture.path.read_bytes(), actor=PRINCIPAL)
            self.assertTrue(result["ok"])
            self.assertEqual(fixture.value(field="owner"), PRINCIPAL)
            self.assertEqual(fixture.store.db.execute("SELECT owner FROM events ORDER BY id DESC LIMIT 1").fetchone()[0], PRINCIPAL)
        finally:
            fixture.tearDown()

    def test_unsafe_paths_are_rejected_before_static_lookup(self):
        with running() as (server, _, _):
            for path in ("/sop/../index.html", "/sop/%2e%2e/index.html", "/sop/%252e%252e/index.html",
                         "/sop/%2fapi/products", "/sop/%5capi/products", "/sop//api/products",
                         "/sop/%00", "/sop/index.html:stream"):
                with self.subTest(path=path):
                    self.assertEqual(request(server, path)[0], 400)

    def test_duplicate_headers_are_rejected(self):
        with running(writes=True) as (server, _, _):
            for duplicated in ("Host", "X-SOP-Proxy-Key", "X-SOP-User", "Origin", "Content-Length"):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.putrequest("POST", "/sop/api/write-access", skip_host=True)
                values = {"Host": f"127.0.0.1:{server.server_port}", "X-SOP-Proxy-Key": KEY,
                          "X-SOP-User": PRINCIPAL, "Origin": ORIGIN, "Content-Length": "0", "Content-Type": "application/json"}
                for name, value in values.items():
                    connection.putheader(name, value)
                    if name == duplicated:
                        connection.putheader(name, value)
                connection.endheaders()
                response = connection.getresponse()
                self.assertIn(response.status, {400, 403})
                response.read()
                connection.close()

    def test_health_does_not_disclose_server_paths_or_pid(self):
        with running() as (server, _, _):
            code, _, body = request(server, "/sop/api/health")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body), {"app_id": "hima-sop", "launch_mode": "gateway"})

    def test_console_behavior_remains_explicit_and_cannot_embed(self):
        with running(gateway_mode=False) as (server, _, _):
            code, headers, body = request(server, "/api/session")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body), {"mode": "console", "user": None, "canWrite": True})
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertEqual(request(server, "/api/session", headers={"Origin": ORIGIN})[0], 403)


class GatewayConfigTest(unittest.TestCase):
    def test_configuration_is_explicit_and_secrets_are_environment_only(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"TEST_SOP_PROXY_KEY": KEY}):
            file = Path(temporary) / "gateway.json"
            raw = {"base_path": "/sop", "public_origin": ORIGIN, "proxy_secret_env": "TEST_SOP_PROXY_KEY"}
            file.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(file)
            self.assertFalse(config.allow_writes)
            self.assertNotIn(KEY, repr(config))
            self.assertEqual(config.public_origin, ORIGIN)
            for updates in ({"base_path": "/"}, {"base_path": "/sop/"}, {"allow_writes": "false"},
                            {"public_origin": "https://host/path"}, {"public_origin": "https://user:pass@host"},
                            {"proxy_secret_env": "MISSING_SOP_KEY"}, {"proxy_secret": KEY}, {"data_dir": "relative"}):
                file.write_text(json.dumps({**raw, **updates}), encoding="utf-8")
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    GatewayConfig.load(file)
            self.assertEqual(canonical_origin("HTTPS://DATA.EXAMPLE.TEST:443"), ORIGIN)

    def test_loopback_is_not_enough_without_proxy_authentication(self):
        headers = Message()
        for name, value in {"Host": "data.example.test", "X-SOP-Proxy-Key": KEY, "X-SOP-User": PRINCIPAL}.items():
            headers[name] = value
        config = GatewayConfig("/sop", ORIGIN, KEY)
        with self.assertRaises(GatewayError):
            config.authorize(headers, "192.0.2.10", 8876, "GET")


if __name__ == "__main__":
    unittest.main()
