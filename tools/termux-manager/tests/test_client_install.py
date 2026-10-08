"""Offline tests for authenticated transport and installation boundaries."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from termux_manager.auth import read_private_key, response_signature, sign_request
from termux_manager.client import Client, ClientError, MAX_BODY, NoRedirect, decode_json, main as client_main, validate_url
from termux_manager.install import DIRECTORIES, FILES, InstallError, install_project, main as install_main
from termux_manager.storage import StoreError

KEY = bytes.fromhex("ab" * 32)
PROJECT = "12" * 16
OBJECT = "34" * 16
REQUEST_ID = "56" * 16


class FakeResponse(io.BytesIO):
    def __init__(self, data, status, signature):
        super().__init__(data)
        self.status = status
        self.headers = {"X-TM-Response-Signature": signature}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = Client("http://phone.tail123.ts.net:8081/", KEY, PROJECT)
        self.requests = []

    def responder(self, data=b'{"ok":true}', status=200, signature=None):
        def open_response(request, timeout):
            self.requests.append((request, timeout))
            nonce = request.get_header("X-tm-nonce")
            actual = response_signature(KEY, nonce, status, data) if signature is None else signature
            return FakeResponse(data, status, actual)
        return open_response

    def test_approved_urls(self):
        for url in ("http://127.0.0.1:8081", "http://127.2.3.4/", "http://100.64.0.1", "https://100.127.255.254", "https://Phone.tail123.ts.net:443/"):
            with self.subTest(url=url):
                self.assertTrue(validate_url(url)[0])

    def test_reject_non_tailnet_and_ambiguous_urls(self):
        urls = ["http://localhost", "http://192.168.1.1", "http://100.128.0.1", "http://100.63.255.255", "http://8.8.8.8", "http://[::1]", "http://example.com", "http://ts.net", "http://fake.ts.net.evil.com", "http://-bad.ts.net", "http://bad_.ts.net", "http://phone.ts.net.", "http://user:pass@phone.ts.net", "http://phone.ts.net/path", "http://phone.ts.net?", "http://phone.ts.net#", "http://phone.ts.net:0", "http://phone.ts.net:65536", "http://phone.ts.net:abc", "ftp://phone.ts.net", "http://phone.ts.net\n", "http://127.1", "http://2130706433", "http://phone.ts.net\\evil"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ClientError):
                validate_url(url)

    def test_only_explicit_loopback_proxy_bypass(self):
        with self.assertRaises(ClientError):
            Client("http://phone.ts.net", KEY, PROJECT, bypass_proxy=True)
        with self.assertRaises(ClientError):
            Client("http://100.64.0.1", KEY, PROJECT, bypass_proxy=True)
        Client("http://127.0.0.1", KEY, PROJECT, bypass_proxy=True)

    def test_health_signs_fixed_path_and_never_transmits_key(self):
        with patch.object(self.client.opener, "open", self.responder()):
            self.assertEqual(self.client.health(), {"ok": True})
        request, timeout = self.requests[0]
        self.assertEqual(request.full_url, "http://phone.tail123.ts.net:8081/v1/health")
        self.assertEqual(timeout, 20)
        self.assertIsNone(request.data)
        headers = dict(request.header_items())
        self.assertNotIn(KEY.hex(), str(headers))
        self.assertEqual(headers["X-tm-signature"], sign_request(KEY, PROJECT, "GET", "/v1/health", headers["X-tm-time"], headers["X-tm-nonce"], b""))

    def test_routes_and_payloads_are_fixed(self):
        with patch.object(self.client.opener, "open", self.responder()):
            self.client.status()
            self.client.upload({"account": None})
            self.client.get_input(OBJECT)
            self.client.scan(OBJECT, REQUEST_ID)
            self.client.job(OBJECT)
        self.assertEqual([request.get_method() for request, _ in self.requests], ["GET", "POST", "GET", "POST", "GET"])
        self.assertEqual(json.loads(self.requests[1][0].data), {"context": {"account": None}})
        self.assertEqual(json.loads(self.requests[3][0].data), {"action": "scan", "input_id": OBJECT, "request_id": REQUEST_ID})

    def test_invalid_requests_never_reach_network(self):
        with patch.object(self.client.opener, "open") as open_mock:
            for call in (lambda: self.client.get_input("../x"), lambda: self.client.job("A" * 32), lambda: self.client.scan(OBJECT, "bad"), lambda: self.client.upload([]), lambda: self.client.upload({"x": float("nan")}), lambda: self.client.upload({"x": "x" * MAX_BODY}), lambda: self.client._request("POST", "/v1/orders", {}), lambda: self.client._request("GET", "/v1/health?x=1")):
                with self.assertRaises(ClientError):
                    call()
            open_mock.assert_not_called()

    def test_response_authentication_precedes_json_parsing(self):
        with patch.object(self.client.opener, "open", self.responder(b"not-json", signature="00" * 32)), patch("termux_manager.client.decode_json") as parse_mock:
            with self.assertRaisesRegex(ClientError, "authentication"):
                self.client.health()
            parse_mock.assert_not_called()

    def test_unicode_or_missing_signature_rejected_cleanly(self):
        for signature in ("", "ü" * 64, "AB" * 32):
            with self.subTest(signature=signature), patch.object(self.client.opener, "open", self.responder(signature=signature)), self.assertRaisesRegex(ClientError, "authentication"):
                self.client.health()

    def test_bad_signed_json_rejected(self):
        for data in (b"invalid", b"[]", b'{"x":NaN}', b'{"x":1,"x":2}'):
            with self.subTest(data=data), patch.object(self.client.opener, "open", self.responder(data)), self.assertRaises(ClientError):
                self.client.health()

    def test_oversized_response_rejected(self):
        with patch.object(self.client.opener, "open", self.responder(b"x" * (MAX_BODY + 1))), self.assertRaisesRegex(ClientError, "128 KiB"):
            self.client.health()

    def test_response_cannot_be_replayed_across_nonce(self):
        signature = response_signature(KEY, "00" * 16, 200, b'{"ok":true}')
        with patch.object(self.client.opener, "open", self.responder(signature=signature)), self.assertRaisesRegex(ClientError, "authentication"):
            self.client.health()

    def test_signed_error_does_not_reveal_error_body(self):
        def error_response(request, timeout):
            data = b'{"error":"secret-text-must-not-escape"}'
            signature = response_signature(KEY, request.get_header("X-tm-nonce"), 409, data)
            raise HTTPError(request.full_url, 409, "Conflict", {"X-TM-Response-Signature": signature}, io.BytesIO(data))
        with patch.object(self.client.opener, "open", error_response), self.assertRaisesRegex(ClientError, "HTTP 409") as caught:
            self.client.health()
        self.assertNotIn("secret-text", str(caught.exception))

    def test_unsigned_error_is_untrusted(self):
        error = HTTPError(self.client.url, 401, "Unauthorized", {}, io.BytesIO(b"any"))
        with patch.object(self.client.opener, "open", side_effect=error), self.assertRaisesRegex(ClientError, "authentication"):
            self.client.health()

    def test_network_error_is_generic(self):
        for error in (URLError("secret-text"), OSError("secret-text"), http.client.IncompleteRead(b"secret-text")):
            with self.subTest(error=type(error)), patch.object(self.client.opener, "open", side_effect=error), self.assertRaisesRegex(ClientError, "Connection failed") as caught:
                self.client.health()
            self.assertNotIn("secret-text", str(caught.exception))

    def test_redirect_is_never_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "found", {}, "http://evil.test"))

    def test_cli_retains_public_request_id_on_uncertain_scan(self):
        stderr = io.StringIO()
        with patch("termux_manager.client.read_private_key", return_value=KEY), patch.object(Client, "scan", side_effect=ClientError("Connection failed")), contextlib.redirect_stderr(stderr):
            result = client_main(["--url", "http://phone.ts.net", "--key-file", "/unused", "--project-id", PROJECT, "scan", "--input-id", OBJECT, "--request-id", REQUEST_ID])
        self.assertEqual(result, 1)
        self.assertIn("request_id=" + REQUEST_ID, stderr.getvalue())
        self.assertNotIn(KEY.hex(), stderr.getvalue())

    def test_cli_key_permissions_error_is_sanitized(self):
        stderr = io.StringIO()
        with patch("termux_manager.client.read_private_key", side_effect=StoreError("unsafe_file")), contextlib.redirect_stderr(stderr):
            result = client_main(["--url", "http://phone.ts.net", "--key-file", "/unused", "--project-id", PROJECT, "health"])
        self.assertEqual(result, 1)
        self.assertIn("invalid or inaccessible local configuration", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in FILES:
            (self.source / name).write_text("{}" if name.endswith(".json") else "source content\n")
        for name in DIRECTORIES:
            (self.source / name).mkdir()
            (self.source / name / "example.py").write_text("VALUE = 1\n")
        self.destination = self.root / "new-project"
        self.public_root = self.root / "existing-http-root"
        self.public_root.mkdir()

    def install(self):
        return install_project(self.destination, source=self.source, public_roots=[self.public_root])

    def test_install_new_only_with_private_keys_and_storage(self):
        old = self.root / "existing-bot.py"
        old.write_bytes(b"existing-content")
        result = self.install()
        self.assertFalse(result["started"])
        self.assertRegex(result["project_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(old.read_bytes(), b"existing-content")
        self.assertEqual(len(read_private_key(self.destination / "auth.key")), 32)
        self.assertEqual(json.loads((self.destination / "project.json").read_text())["project_id"], result["project_id"])
        self.assertEqual(json.loads((self.destination / "project.json").read_text())["public_roots"], [str(self.public_root.resolve())])
        for path in (self.destination, self.destination / "runtime", *(self.destination / "runtime" / name for name in ("inputs", "jobs", "results", "nonces"))):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for filename in ("auth.key", "project.json"):
            self.assertEqual(stat.S_IMODE((self.destination / filename).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.destination / "start.sh").stat().st_mode), 0o700)
        script = (self.destination / "start.sh").read_text()
        self.assertIn('python3 -I "$APP_DIR/launch.py"', script)
        self.assertNotIn((self.destination / "auth.key").read_text().strip(), script)

    def test_existing_directory_untouched(self):
        self.destination.mkdir()
        sentinel = self.destination / "sentinel"
        sentinel.write_bytes(b"keep")
        with self.assertRaisesRegex(InstallError, "already exists"):
            self.install()
        self.assertEqual(list(self.destination.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_existing_file_untouched(self):
        self.destination.write_text("keep")
        with self.assertRaises(InstallError):
            self.install()
        self.assertEqual(self.destination.read_text(), "keep")

    def test_destination_symlink_and_dangling_symlink_rejected(self):
        for target in (self.source, self.root / "missing"):
            with self.subTest(target=target):
                self.destination.symlink_to(target)
                with self.assertRaises(InstallError):
                    self.install()
                self.assertTrue(self.destination.is_symlink())
                self.destination.unlink()

    def test_source_symlink_rejected_before_destination_creation(self):
        (self.source / "docs" / "linked").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(InstallError, "symlink"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_source_directory_symlink_rejected_before_destination_creation(self):
        (self.source / "docs" / "example.py").unlink()
        (self.source / "docs").rmdir()
        (self.source / "docs").symlink_to(self.root)
        with self.assertRaises(InstallError):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_source_special_file_rejected_before_destination_creation(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("Unix FIFO unavailable")
        os.mkfifo(self.source / "docs" / "fifo")
        with self.assertRaises(InstallError):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_missing_source_file_rejected_before_destination_creation(self):
        (self.source / "launch.py").unlink()
        with self.assertRaises(InstallError):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_no_runtime_creation_without_required_timezone(self):
        with patch("termux_manager.install._preflight_runtime", side_effect=InstallError("timezone missing")), self.assertRaises(InstallError):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_missing_destination_parent_not_created(self):
        self.destination = self.root / "missing-parent" / "new-project"
        with self.assertRaises(InstallError):
            self.install()
        self.assertFalse(self.destination.parent.exists())

    def test_public_root_declaration_is_required(self):
        with self.assertRaisesRegex(InstallError, "Declare every"):
            install_project(self.destination, source=self.source, public_roots=[])
        self.assertFalse(self.destination.exists())
        with self.assertRaises(TypeError):
            install_project(self.destination, source=self.source)

    def test_install_below_http_document_root_rejected_before_secrets(self):
        self.destination = self.public_root / "would-expose-key"
        with patch("termux_manager.install.secrets.token_hex") as generate, self.assertRaisesRegex(InstallError, "HTTP document root"):
            self.install()
        generate.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_install_equal_http_document_root_rejected(self):
        self.destination = self.public_root
        with self.assertRaisesRegex(InstallError, "HTTP document root"):
            self.install()
        self.assertEqual(list(self.public_root.iterdir()), [])

    def test_ancestor_http_document_root_rejected(self):
        self.public_root = self.root
        with self.assertRaisesRegex(InstallError, "HTTP document root"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_multiple_http_document_roots_are_all_checked(self):
        second_root = self.root / "another-http-root"
        second_root.mkdir()
        self.destination = second_root / "would-expose-key"
        with self.assertRaisesRegex(InstallError, "HTTP document root"):
            install_project(self.destination, source=self.source, public_roots=[self.public_root, second_root])
        self.assertFalse(self.destination.exists())

    def test_http_document_root_symlink_resolved_before_check(self):
        alias = self.root / "http-root-alias"
        alias.symlink_to(self.public_root)
        self.destination = self.public_root / "would-expose-key"
        self.public_root = alias
        with self.assertRaisesRegex(InstallError, "HTTP document root"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_destination_parent_symlink_resolved_before_public_root_check(self):
        alias = self.root / "private-looking-alias"
        alias.symlink_to(self.public_root)
        self.destination = alias / "would-expose-key"
        with self.assertRaisesRegex(InstallError, "HTTP document root"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_invalid_http_document_roots_rejected_before_creation(self):
        regular_file = self.root / "not-a-directory"
        regular_file.write_text("keep")
        for invalid in (self.root / "missing-public-root", regular_file):
            with self.subTest(root=invalid), self.assertRaisesRegex(InstallError, "existing directory"):
                install_project(self.destination, source=self.source, public_roots=[invalid])
            self.assertFalse(self.destination.exists())

    def test_public_alias_to_destination_parent_rejected_before_key_generation(self):
        alias = self.public_root / "existing-alias"
        alias.symlink_to(self.destination.parent)
        sentinel = self.public_root / "untouched.txt"
        sentinel.write_text("keep")
        with patch("termux_manager.install.secrets.token_hex") as generate, self.assertRaisesRegex(InstallError, "public-tree alias"):
            self.install()
        generate.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertTrue(alias.is_symlink())

    def test_public_alias_chain_through_other_directory_rejected(self):
        shared = self.root / "shared"
        shared.mkdir()
        (self.public_root / "shared-alias").symlink_to(shared)
        (shared / "private-alias").symlink_to(self.destination.parent)
        with self.assertRaisesRegex(InstallError, "public-tree alias"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_public_hidden_subdirectory_alias_rejected(self):
        nested = self.public_root / ".hidden" / "nested"
        nested.mkdir(parents=True)
        (nested / "alias").symlink_to(self.destination.parent)
        with self.assertRaisesRegex(InstallError, "public-tree alias"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_dangling_public_links_to_future_project_or_key_rejected(self):
        for target in (self.destination, self.destination / "auth.key", self.destination / "runtime" / "inputs"):
            with self.subTest(target=target):
                alias = self.public_root / "future-alias"
                alias.symlink_to(target)
                with patch("termux_manager.install.secrets.token_hex") as generate, self.assertRaisesRegex(InstallError, "public-tree alias"):
                    self.install()
                generate.assert_not_called()
                self.assertFalse(self.destination.exists())
                alias.unlink()

    def test_dangling_link_chain_to_future_key_rejected(self):
        intermediate = self.root / "intermediate-link"
        intermediate.symlink_to(self.destination)
        (self.public_root / "key-alias").symlink_to(intermediate / "auth.key")
        with self.assertRaisesRegex(InstallError, "public-tree alias"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_unrelated_public_directory_and_dangling_aliases_allowed(self):
        shared = self.root / "shared"
        shared.mkdir()
        (shared / "file.txt").write_text("public")
        (self.public_root / "shared-alias").symlink_to(shared)
        (self.public_root / "unrelated-dangling").symlink_to(self.root / "unrelated-missing")
        self.install()
        self.assertTrue((self.destination / "auth.key").exists())

    def test_directory_alias_cycles_terminate_without_skipping_other_paths(self):
        (self.public_root / "self-alias").symlink_to(self.public_root)
        child = self.public_root / "child"
        child.mkdir()
        (child / "back-alias").symlink_to(self.public_root)
        self.install()
        self.assertTrue((self.destination / "auth.key").exists())

    def test_unresolvable_symlink_loop_fails_closed(self):
        (self.public_root / "loop-a").symlink_to(self.public_root / "loop-b")
        (self.public_root / "loop-b").symlink_to(self.public_root / "loop-a")
        with self.assertRaisesRegex(InstallError, "fully verified"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_public_tree_entry_budget_fails_before_creation(self):
        for name in ("a", "b", "c"):
            (self.public_root / name).write_text("public")
        with patch("termux_manager.install.MAX_PUBLIC_TREE_ENTRIES", 2), self.assertRaisesRegex(InstallError, "fully verified"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_public_tree_depth_budget_fails_before_creation(self):
        (self.public_root / "a" / "b").mkdir(parents=True)
        with patch("termux_manager.install.MAX_PUBLIC_TREE_DEPTH", 1), self.assertRaisesRegex(InstallError, "fully verified"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_public_tree_time_budget_fails_before_creation(self):
        with patch("termux_manager.install.time.monotonic", side_effect=[0, 100]), self.assertRaisesRegex(InstallError, "fully verified"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_unreadable_public_tree_fails_before_creation(self):
        actual_scandir = os.scandir
        def deny_public_root(path):
            if Path(path) == self.public_root:
                raise PermissionError("test denied")
            return actual_scandir(path)
        with patch("termux_manager.install.os.scandir", side_effect=deny_public_root), self.assertRaisesRegex(InstallError, "fully verified"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_cli_public_root_flag_is_required(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            install_main(["--dest", str(self.destination)])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--public-root", stderr.getvalue())
        self.assertFalse(self.destination.exists())

    def test_cli_discovery_supplies_verified_roots_to_installer(self):
        fake_result = {"destination": str(self.destination), "project_id": PROJECT, "started": False}
        with patch("termux_manager.discovery.discover_http_roots", return_value=[self.public_root]) as discover, patch("termux_manager.install.install_project", return_value=fake_result) as install, contextlib.redirect_stdout(io.StringIO()):
            result = install_main(["--dest", str(self.destination), "--discover-http-port", "8080"])
        self.assertEqual(result, 0)
        discover.assert_called_once_with(8080)
        install.assert_called_once_with(self.destination, public_roots=[self.public_root])

    def test_cli_explicit_and_discovered_roots_are_merged(self):
        second_root = self.root / "second-public-root"
        second_root.mkdir()
        fake_result = {"destination": str(self.destination), "project_id": PROJECT, "started": False}
        with patch("termux_manager.discovery.discover_http_roots", return_value=[second_root]) as discover, patch("termux_manager.install.install_project", return_value=fake_result) as install, contextlib.redirect_stdout(io.StringIO()):
            result = install_main(["--dest", str(self.destination), "--public-root", str(self.public_root), "--discover-http-port", "8080"])
        self.assertEqual(result, 0)
        discover.assert_called_once_with(8080)
        install.assert_called_once_with(self.destination, public_roots=[self.public_root, second_root])

    def test_cli_discovery_failure_does_not_create_project(self):
        from termux_manager.discovery import DiscoveryError
        stderr = io.StringIO()
        with patch("termux_manager.discovery.discover_http_roots", side_effect=DiscoveryError("not_found")), patch("termux_manager.install.install_project") as install, contextlib.redirect_stderr(stderr):
            result = install_main(["--dest", str(self.destination), "--discover-http-port", "8080"])
        self.assertEqual(result, 1)
        self.assertIn("discovery failed", stderr.getvalue())
        install.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_cli_invalid_discovery_port_fails_before_discovery_or_install(self):
        for port in ("0", "65536", "-1", "not-a-port"):
            with self.subTest(port=port), patch("termux_manager.discovery.discover_http_roots") as discover, patch("termux_manager.install.install_project") as install, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                install_main(["--dest", str(self.destination), "--discover-http-port", port])
            self.assertEqual(caught.exception.code, 2)
            discover.assert_not_called()
            install.assert_not_called()

    def test_bytecode_not_copied(self):
        cache = self.source / "termux_manager" / "__pycache__"
        cache.mkdir()
        (cache / "module.pyc").write_bytes(b"cache")
        self.install()
        self.assertFalse((self.destination / "termux_manager" / "__pycache__").exists())

    def test_installs_generate_distinct_keys_and_ids(self):
        first = self.install()
        first_key = (self.destination / "auth.key").read_bytes()
        self.destination = self.root / "another-project"
        second = self.install()
        self.assertNotEqual(first["project_id"], second["project_id"])
        self.assertNotEqual(first_key, (self.destination / "auth.key").read_bytes())

    def test_cli_output_contains_no_generated_key(self):
        fake_result = {"destination": str(self.destination), "project_id": PROJECT, "started": False}
        stdout = io.StringIO()
        with patch("termux_manager.install.install_project", return_value=fake_result), contextlib.redirect_stdout(stdout):
            result = install_main(["--dest", str(self.destination), "--public-root", str(self.public_root)])
        self.assertEqual(result, 0)
        self.assertIn(PROJECT, stdout.getvalue())
        self.assertNotIn(KEY.hex(), stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
