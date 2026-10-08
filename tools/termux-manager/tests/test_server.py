import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from termux_manager.auth import response_signature, sign_request
from termux_manager.jobs import Jobs
from termux_manager.server import make_server, peer_allowed, validate_bind
from termux_manager.storage import MAX_JSON_BYTES, Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        for namespace in ("inputs", "jobs", "results", "nonces"):
            (self.root / namespace).mkdir(mode=0o700)
        self.store = Store(self.root)
        self.key = secrets.token_bytes(32)
        self.project = secrets.token_hex(16)
        self.jobs = Jobs(self.store, interval=0)
        self.server = make_server("127.0.0.1", 0, self.key, self.project, self.store, self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.temporary.cleanup()

    def headers(self, method, path, body=b"", *, nonce=None, timestamp=None, key=None):
        nonce = nonce or secrets.token_hex(16)
        timestamp = timestamp or str(int(time.time()))
        values = {"X-TM-Project": self.project, "X-TM-Time": timestamp,
                  "X-TM-Nonce": nonce,
                  "X-TM-Signature": sign_request(key or self.key, self.project, method, path, timestamp, nonce, body)}
        return nonce, values

    def request(self, method="GET", path="/v1/health", payload=None, *, body=None, extra=None,
                nonce=None, timestamp=None, key=None, signed=True):
        if body is None:
            body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        nonce, headers = self.headers(method, path, body, nonce=nonce, timestamp=timestamp, key=key)
        if not signed:
            headers = {}
        headers["Content-Length"] = str(len(body))
        if method == "POST":
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            raw = response.read()
            signature = response.getheader("X-TM-Response-Signature")
            if signature is not None:
                self.assertEqual(signature, response_signature(self.key, nonce, response.status, raw))
            self.assertEqual(response.getheader("Connection"), "close")
            return response.status, json.loads(raw), signature
        finally:
            connection.close()

    def raw_request(self, raw):
        connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        try:
            connection.sendall(raw)
            chunks = []
            while True:
                try:
                    chunk = connection.recv(65536)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            connection.close()

    def wire(self, method="GET", path="/v1/health", body=b"", extra=()):
        nonce, headers = self.headers(method, path, body)
        pairs = [("Host", "localhost"), ("Content-Length", str(len(body))), *headers.items(), *extra]
        raw = (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in pairs) + "\r\n").encode()
        return nonce, raw + body

    def test_health_is_authenticated_and_exact_response_signed(self):
        status, result, signature = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result, {"ok": True, "execution_enabled": False})
        self.assertIsNotNone(signature)

    def test_missing_wrong_expired_and_replayed_auth_are_unsigned(self):
        for options in ({"signed": False}, {"key": b"x" * 32},
                        {"timestamp": str(int(time.time()) - 90)}):
            with self.subTest(options=tuple(options)):
                status, result, signature = self.request(**options)
                self.assertEqual(status, 401)
                self.assertEqual(result["error"], "authentication_required")
                self.assertIsNone(signature)
        nonce = secrets.token_hex(16)
        self.assertEqual(self.request(nonce=nonce)[0], 200)
        status, _, signature = self.request(nonce=nonce)
        self.assertEqual(status, 401)
        self.assertIsNone(signature)

    def test_upload_read_scan_without_credentials_yields_pas(self):
        status, uploaded, _ = self.request("POST", "/v1/inputs", {"context": {}})
        self.assertEqual(status, 201)
        object_id = uploaded["id"]
        status, read, _ = self.request(path="/v1/inputs/" + object_id)
        self.assertEqual(status, 200)
        self.assertEqual(read["context"], {})
        request_id = secrets.token_hex(16)
        with patch.dict(os.environ, {}, clear=True):
            status, job, _ = self.request("POST", "/v1/jobs", {"action": "scan", "input_id": object_id, "request_id": request_id})
            self.assertEqual(status, 202)
            self.jobs.worker.join(timeout=2)
        self.assertEqual(job["id"], request_id)
        status, result, _ = self.request(path="/v1/jobs/" + request_id)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["result"]["decision"], "PAS")
        self.assertIs(result["result"]["execution_enabled"], False)
        status, state, _ = self.request(path="/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(state["counts"]["inputs"], 1)
        self.assertEqual(state["counts"]["results"], 1)
        self.assertIsNone(state["current_job"])

    def test_idempotent_job_request_never_reruns(self):
        calls = []
        self.jobs.runner = lambda context: (calls.append(context) or {"decision": "PAS", "execution_enabled": False})
        _, uploaded, _ = self.request("POST", "/v1/inputs", {"context": {}})
        payload = {"action": "scan", "input_id": uploaded["id"], "request_id": secrets.token_hex(16)}
        self.assertEqual(self.request("POST", "/v1/jobs", payload)[0], 202)
        self.jobs.worker.join(timeout=2)
        self.assertEqual(self.request("POST", "/v1/jobs", payload)[0], 202)
        self.assertEqual(len(calls), 1)

    def test_signed_invalid_json_and_context_do_not_create_objects(self):
        for body in (b'{"context":{},"context":{}}', b'{"context":{"fx":{"usdtry_ask":NaN}}}',
                     b'{"context":[],"command":"id"}', b'{"context":{"command":"id"}}', b'[]'):
            with self.subTest(body=body):
                status, _, signature = self.request("POST", "/v1/inputs", body=body)
                self.assertEqual(status, 400)
                self.assertIsNotNone(signature)
        self.assertEqual(self.store.counts()["inputs"], 0)

    def test_stored_object_response_wrapper_cannot_exceed_response_cap(self):
        object_id = secrets.token_hex(16)
        # An object can fit its storage cap while its response envelope cannot.
        self.store.create("inputs", object_id, {"padding": "x" * (MAX_JSON_BYTES - 14)})
        status, result, signature = self.request(path="/v1/inputs/" + object_id)
        self.assertEqual(status, 413)
        self.assertEqual(result, {"error": "response_too_large", "execution_enabled": False})
        self.assertIsNotNone(signature)

    def test_unknown_routes_commands_and_orders_are_unavailable(self):
        for path in ("/v1/orders", "/v1/exec", "/v1/upload", "/v1/files", "/auth.key"):
            status, _, signature = self.request("POST", path, {"command": "id"})
            self.assertEqual(status, 404)
            self.assertIsNotNone(signature)
        status, result, _ = self.request("POST", "/v1/jobs", {"action": "exec"})
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "only_scan_action_allowed")
        self.assertEqual(self.store.counts()["jobs"], 0)

    def test_methods_and_signed_route_validation_errors(self):
        status, _, signature = self.request("DELETE")
        self.assertEqual(status, 405)
        self.assertIsNotNone(signature)
        for path in ("/v1/../auth.key", "/v1/%2e%2e/auth.key", "/v1/health?x=1", "/v1\\health", "//v1/health"):
            with self.subTest(path=path):
                status, _, signature = self.request(path=path)
                self.assertEqual(status, 400)
                self.assertIsNotNone(signature)
        self.assertEqual(self.request(path="/v1/inputs/" + "a" * 32)[0], 404)

    def test_signed_get_body_origin_and_content_type_are_rejected(self):
        for method, body, extra, status in (("GET", b"{}", {}, 400),
                                            ("GET", b"", {"Origin": "https://example.com"}, 403),
                                            ("POST", b"{}", {"Content-Type": "text/plain"}, 415)):
            result_status, _, signature = self.request(method, body=body, extra=extra)
            self.assertEqual(result_status, status)
            self.assertIsNotNone(signature)

    def test_duplicate_security_headers_are_rejected_before_auth(self):
        for name, value in (("X-TM-Nonce", "b" * 32), ("Host", "other"), ("Content-Length", "0"),
                            ("x-tm-signature", "c" * 64)):
            with self.subTest(name=name):
                _, raw = self.wire(extra=[(name, value)])
                response = self.raw_request(raw)
                self.assertIn(b" 401 ", response.split(b"\r\n", 1)[0])
                self.assertNotIn(b"X-TM-Response-Signature:", response)

    def test_ambiguous_framing_and_oversize_are_rejected_without_body_read(self):
        for extra in ([('Transfer-Encoding', 'chunked')], [('Content-Encoding', 'gzip')],
                      [('Expect', '100-continue')]):
            _, raw = self.wire(extra=extra)
            response = self.raw_request(raw)
            self.assertTrue(response.startswith(b"HTTP/1.0 401 "))
        for length in (str(MAX_JSON_BYTES + 1), "-1", "0, 0", "1e2"):
            _, raw = self.wire()
            raw = raw.replace(b"Content-Length: 0", b"Content-Length: " + length.encode())
            self.assertTrue(self.raw_request(raw).startswith(b"HTTP/1.0 401 "))

    def test_header_budget_rejects_large_headers(self):
        _, raw = self.wire(extra=[("X-Padding", "z" * 20000)])
        response = self.raw_request(raw)
        self.assertTrue(response.startswith(b"HTTP/1.0 401 "))

    def test_no_keepalive_or_pipeline(self):
        _, first = self.wire(extra=[("Connection", "keep-alive")])
        _, second = self.wire()
        response = self.raw_request(first + second)
        self.assertEqual(response.count(b"HTTP/1.0 200 "), 1)

    def test_runtime_lock_prevents_second_manager_and_releases_on_close(self):
        another_store = Store(self.root)
        try:
            with self.assertRaisesRegex(ValueError, "manager_already_running"):
                make_server("127.0.0.1", 0, self.key, self.project, another_store)
            self.server.shutdown()
            self.server.server_close()
            replacement = make_server("127.0.0.1", 0, self.key, self.project, another_store)
            replacement.server_close()
        finally:
            another_store.close()

    def test_absolute_deadline_stops_dripping_headers(self):
        with patch("termux_manager.server.CONNECTION_DEADLINE", 0.25), patch("termux_manager.server.SOCKET_TIMEOUT", 0.15):
            connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
            started = time.monotonic()
            try:
                connection.sendall(b"GET /v1/health HTTP/1.1\r\nX-Slow: ")
                for _ in range(20):
                    time.sleep(0.04)
                    try:
                        connection.sendall(b"a")
                    except OSError:
                        break
                self.assertLess(time.monotonic() - started, 0.7)
                try:
                    self.assertEqual(connection.recv(4096), b"")
                except ConnectionResetError:
                    pass
            finally:
                connection.close()

    def test_absolute_deadline_stops_dripping_body(self):
        with patch("termux_manager.server.CONNECTION_DEADLINE", 0.25), patch("termux_manager.server.SOCKET_TIMEOUT", 0.15):
            connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
            _, raw = self.wire("POST", "/v1/inputs", b"x" * 100, [("Content-Type", "application/json")])
            try:
                connection.sendall(raw[:-100])
                started = time.monotonic()
                for _ in range(20):
                    time.sleep(0.04)
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        break
                self.assertLess(time.monotonic() - started, 0.7)
            finally:
                connection.close()
        self.assertEqual(self.store.counts()["inputs"], 0)

    def test_only_four_handlers_run_at_once(self):
        connections = []
        try:
            for _ in range(4):
                connection = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
                connection.sendall(b"GET /v1/health HTTP/1.1\r\nX-Wait: ")
                connections.append(connection)
            deadline = time.monotonic() + 1
            while self.server._slots._value and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.server._slots._value, 0)
            _, request = self.wire()
            self.assertEqual(self.raw_request(request), b"")
        finally:
            for connection in connections:
                connection.close()


class BindTests(unittest.TestCase):
    def test_peer_policy_uses_only_ipv4_loopback_and_tailnet_range(self):
        for value in ("127.0.0.1", "127.0.1.5", "100.64.0.1", "100.127.255.254"):
            self.assertTrue(peer_allowed(value))
        for value in ("100.63.255.255", "100.128.0.1", "192.168.1.1", "8.8.8.8", "::1", "invalid"):
            self.assertFalse(peer_allowed(value))

    def test_bind_requires_explicit_tailnet_permission_and_no_public_lan_bind(self):
        validate_bind("127.0.0.1")
        for host in ("0.0.0.0", "100.64.1.2"):
            with self.assertRaises(ValueError):
                validate_bind(host)
            validate_bind(host, True)
        for host in ("8.8.8.8", "192.168.1.2", "localhost", "::1", "example.ts.net"):
            with self.assertRaises(ValueError):
                validate_bind(host, True)

    def test_forwarded_header_does_not_override_socket_peer(self):
        # ManagementServer calls this on the socket tuple, without consulting headers.
        from termux_manager.server import ManagementServer
        self.assertFalse(ManagementServer.verify_request(None, object(), ("192.168.1.2", 123)))
        self.assertTrue(ManagementServer.verify_request(None, object(), ("100.64.1.2", 123)))


if __name__ == "__main__":
    unittest.main()
