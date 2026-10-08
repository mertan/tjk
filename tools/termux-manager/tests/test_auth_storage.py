import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch

from termux_manager.auth import (
    Authenticator, AuthError, read_private_key, response_signature, sign_request,
)
from termux_manager.storage import (
    MAX_JSON_BYTES, MAX_OBJECTS, Store, StoreError, decode_json,
)


def install_layout(root):
    root.mkdir(mode=0o700)
    for name in ("inputs", "jobs", "results", "nonces"):
        (root / name).mkdir(mode=0o700)


class PrivateStoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "runtime"
        install_layout(self.root)
        self.store = Store(self.root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def assertStoreError(self, code, fn, *args):
        with self.assertRaises(StoreError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)


class StoreTests(PrivateStoreCase):
    def test_roundtrip_and_private_modes(self):
        object_id = secrets.token_hex(16)
        self.assertFalse(self.store.exists("inputs", object_id))
        self.store.create("inputs", object_id, {"a": [True, None, 1.25], "b": "Türkçe"})
        self.assertEqual(self.store.read("inputs", object_id)["b"], "Türkçe")
        self.assertTrue(self.store.exists("inputs", object_id))
        self.assertEqual((self.root / "inputs" / (object_id + ".json")).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.counts(), {"inputs": 1, "jobs": 0, "results": 0, "nonces": 0})

    def test_no_overwrite(self):
        object_id = "a" * 32
        self.store.create("inputs", object_id, {"original": True})
        self.assertStoreError("already_exists", self.store.create, "inputs", object_id, {"overwrite": True})
        self.assertEqual(self.store.read("inputs", object_id), {"original": True})

    def test_namespaces_and_paths_never_escape(self):
        for namespace in ("../", "nonces", "inputs/../results", "/tmp"):
            with self.subTest(namespace=namespace):
                self.assertStoreError("invalid_namespace", self.store.create, namespace, "a" * 32, {})
        for object_id in ("../outside", "A" * 32, "a" * 31, "a" * 33, "a" * 32 + "\n", "a/b", ""):
            with self.subTest(object_id=object_id):
                self.assertStoreError("invalid_id", self.store.read, "inputs", object_id)

    def test_symlink_file_cannot_read_or_overwrite_outside(self):
        outside = self.base / "outside"
        outside.write_text('{"secret":42}')
        os.chmod(outside, 0o600)
        (self.root / "inputs" / ("a" * 32 + ".json")).symlink_to(outside)
        self.assertStoreError("unsafe_file", self.store.read, "inputs", "a" * 32)
        self.assertStoreError("already_exists", self.store.create, "inputs", "a" * 32, {})
        self.assertEqual(outside.read_text(), '{"secret":42}')

    def test_hardlinked_file_rejected(self):
        outside = self.base / "outside"
        outside.write_text('{}')
        os.chmod(outside, 0o600)
        os.link(outside, self.root / "inputs" / ("a" * 32 + ".json"))
        self.assertStoreError("unsafe_file", self.store.read, "inputs", "a" * 32)

    def test_fifo_read_fails_without_blocking(self):
        os.mkfifo(self.root / "inputs" / ("a" * 32 + ".json"), 0o600)
        self.assertStoreError("unsafe_file", self.store.read, "inputs", "a" * 32)

    def test_world_readable_files_rejected(self):
        self.store.create("inputs", "a" * 32, {})
        os.chmod(self.root / "inputs" / ("a" * 32 + ".json"), 0o644)
        self.assertStoreError("unsafe_file", self.store.read, "inputs", "a" * 32)

    def test_owner_mismatch_rejected(self):
        self.store.create("inputs", "a" * 32, {})
        with patch("termux_manager.storage.os.getuid", return_value=os.getuid() + 1):
            self.assertStoreError("unsafe_directory", self.store.read, "inputs", "a" * 32)

    def test_changed_directory_permissions_fail_closed(self):
        os.chmod(self.root / "inputs", 0o755)
        self.assertStoreError("unsafe_directory", self.store.create, "inputs", "a" * 32, {})

    def test_constructor_rejects_unsafe_root(self):
        os.chmod(self.root, 0o755)
        self.assertStoreError("unsafe_directory", Store, self.root)

    def test_constructor_rejects_symlink_root_and_parent(self):
        link = self.base / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertStoreError("unsafe_directory", Store, link)
        parent_link = self.base / "parent"
        parent_link.symlink_to(self.base, target_is_directory=True)
        self.assertStoreError("unsafe_directory", Store, parent_link / "runtime")

    def test_constructor_rejects_symlink_namespace(self):
        (self.root / "jobs").rmdir()
        (self.root / "jobs").symlink_to(self.root / "inputs", target_is_directory=True)
        self.assertStoreError("unsafe_directory", Store, self.root)

    def test_root_rename_cannot_redirect_writes(self):
        relocated = self.base / "original"
        self.root.rename(relocated)
        install_layout(self.root)
        self.store.create("inputs", "a" * 32, {"pinned": True})
        self.assertTrue((relocated / "inputs" / ("a" * 32 + ".json")).is_file())
        self.assertEqual(list((self.root / "inputs").iterdir()), [])

    def test_namespace_replacement_cannot_redirect_writes(self):
        pinned = self.root / "original-inputs"
        (self.root / "inputs").rename(pinned)
        (self.root / "inputs").symlink_to(self.base, target_is_directory=True)
        self.store.create("inputs", "a" * 32, {"pinned": True})
        self.assertTrue((pinned / ("a" * 32 + ".json")).is_file())
        self.assertFalse((self.base / ("a" * 32 + ".json")).exists())

    def test_invalid_json_create(self):
        for value in ({"a": float("nan")}, {"a": float("inf")}, {1: "bad key"},
                      {"x": (1, 2)}, {"x": object()}, {"x": "\ud800"}, []):
            with self.subTest(value=repr(value)):
                self.assertStoreError("invalid_json", self.store.create, "inputs", "a" * 32, value)
        self.assertEqual(self.store.counts()["inputs"], 0)

    def test_oversized_objects_are_not_written(self):
        self.assertStoreError("object_too_large", self.store.create, "inputs", "a" * 32,
                              {"x": "a" * MAX_JSON_BYTES})
        self.assertEqual(self.store.counts()["inputs"], 0)

    def test_duplicate_and_nonfinite_disk_json_rejected(self):
        path = self.root / "inputs" / ("a" * 32 + ".json")
        for value in (b'{"a":1,"a":2}', b'{"a":1e999}', b'{"a":NaN}', b'[]', b'{bad'):
            path.write_bytes(value)
            os.chmod(path, 0o600)
            self.assertStoreError("invalid_json", self.store.read, "inputs", "a" * 32)

    def test_storage_quota(self):
        for i in range(MAX_OBJECTS):
            self.store.create("inputs", f"{i:032x}", {})
        self.assertStoreError("object_quota", self.store.create, "inputs", "f" * 32, {})
        self.store.create("results", "f" * 32, {})
        self.assertEqual(self.store.counts()["inputs"], MAX_OBJECTS)

    def test_quota_shared_across_store_instances(self):
        other = Store(self.root)
        self.addCleanup(other.close)
        outcomes = []
        gate = threading.Barrier(2)
        def create(store, object_id):
            gate.wait()
            try:
                store.create("jobs", object_id, {})
                outcomes.append("created")
            except StoreError as exc:
                outcomes.append(exc.code)
        with patch("termux_manager.storage.MAX_OBJECTS", 1):
            threads = [threading.Thread(target=create, args=(store, str(i) * 32))
                       for i, store in enumerate((self.store, other))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["created", "object_quota"])

    def test_unexpected_files_fail_closed_without_removal(self):
        existing = self.root / "inputs" / "existing.txt"
        existing.write_text("untouched")
        self.assertStoreError("storage_corrupt", self.store.create, "inputs", "a" * 32, {})
        self.assertEqual(existing.read_text(), "untouched")

    def test_missing_layout_is_not_created(self):
        target = self.base / "absent"
        self.assertStoreError("unsafe_directory", Store, target)
        self.assertFalse(target.exists())

    def test_json_decoder_limits(self):
        for raw in (b'[]', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}',
                    b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}', b'{"x":"\\ud800"}',
                    b'{"x":' + b'[' * 66 + b'0' + b']' * 66 + b'}'):
            with self.subTest(raw=raw[:60]):
                self.assertStoreError("invalid_json", decode_json, raw)
        self.assertStoreError("object_too_large", decode_json, b' ' * (MAX_JSON_BYTES + 1))

    def test_closed_store_fails(self):
        self.store.close()
        self.assertStoreError("store_closed", self.store.create, "inputs", "a" * 32, {})


class NonceTests(PrivateStoreCase):
    def test_replay_survives_restart(self):
        self.assertTrue(self.store.reserve_nonce("a" * 32, 1000))
        self.store.close()
        with Store(self.root) as reopened:
            self.assertFalse(reopened.reserve_nonce("a" * 32, 1001))

    def test_retention_at_least_ninety_seconds(self):
        self.assertTrue(self.store.reserve_nonce("a" * 32, 1000))
        self.assertFalse(self.store.reserve_nonce("a" * 32, 1090))
        self.assertTrue(self.store.reserve_nonce("a" * 32, 1091))

    def test_clock_rollback_does_not_expire_nonce(self):
        self.assertTrue(self.store.reserve_nonce("a" * 32, 1000))
        self.assertFalse(self.store.reserve_nonce("a" * 32, 990))
        self.assertStoreError("clock_rollback", self.store.reserve_nonce, "a" * 32, 900)

    def test_rollback_cannot_revive_previously_expired_replay(self):
        self.assertTrue(self.store.reserve_nonce("a" * 32, 1000))
        self.assertTrue(self.store.reserve_nonce("b" * 32, 1091))
        self.store.close()
        with Store(self.root) as reopened:
            self.assertStoreError("clock_rollback", reopened.reserve_nonce, "a" * 32, 1030)

    def test_quota_fails_closed_and_expired_cleanup_recovers(self):
        with patch("termux_manager.storage.MAX_NONCES", 2):
            self.assertTrue(self.store.reserve_nonce("a" * 32, 1000))
            self.assertTrue(self.store.reserve_nonce("b" * 32, 1000))
            self.assertStoreError("nonce_quota", self.store.reserve_nonce, "c" * 32, 1000)
            self.assertTrue(self.store.reserve_nonce("c" * 32, 1091))
        self.assertEqual(self.store.counts()["nonces"], 1)

    def test_nonce_symlink_rejected_without_deleting_target(self):
        outside = self.base / "outside"
        outside.write_text("1\n")
        os.chmod(outside, 0o600)
        (self.root / "nonces" / ("a" * 32 + ".nonce")).symlink_to(outside)
        self.assertStoreError("unsafe_file", self.store.reserve_nonce, "b" * 32, 1000)
        self.assertEqual(outside.read_text(), "1\n")

    def test_nonce_hardlink_rejected(self):
        outside = self.base / "outside"
        outside.write_text("1\n")
        os.chmod(outside, 0o600)
        os.link(outside, self.root / "nonces" / ("a" * 32 + ".nonce"))
        self.assertStoreError("unsafe_file", self.store.reserve_nonce, "b" * 32, 1000)

    def test_unknown_nonce_files_never_removed(self):
        existing = self.root / "nonces" / "do-not-touch"
        existing.write_text("1\n")
        self.assertStoreError("storage_corrupt", self.store.reserve_nonce, "b" * 32, 1000)
        self.assertTrue(existing.exists())

    def test_parallel_nonce_acceptance_once(self):
        other = Store(self.root)
        self.addCleanup(other.close)
        outcomes = []
        threads = [threading.Thread(target=lambda store=store: outcomes.append(
            store.reserve_nonce("a" * 32, 1000))) for store in (self.store, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, [True, False])


class AuthTests(PrivateStoreCase):
    def setUp(self):
        super().setUp()
        self.key = bytes(range(32))
        self.project = "1" * 32
        self.auth = Authenticator(self.key, self.project, self.store)

    def headers(self, method="GET", path="/v1/health", body=b"", timestamp="1000", nonce=None):
        nonce = nonce or secrets.token_hex(16)
        return {"X-TM-Project": self.project, "X-TM-Time": timestamp, "X-TM-Nonce": nonce,
                "X-TM-Signature": sign_request(self.key, self.project, method, path, timestamp, nonce, body)}

    def assertAuthError(self, code, method, path, headers, body=b"", now=1000):
        with self.assertRaises(AuthError) as caught:
            self.auth.verify(method, path, headers, body, now=now)
        self.assertEqual(caught.exception.code, code)

    def test_known_canonical_signature(self):
        nonce = "a" * 32
        body = b'{"x":1}'
        canonical = ("TM1\n" + self.project + "\nPOST\n/v1/inputs\n1000\n" + nonce
                     + "\n" + hashlib.sha256(body).hexdigest()).encode()
        expected = hmac.new(self.key, canonical, hashlib.sha256).hexdigest()
        self.assertEqual(sign_request(self.key, self.project, "POST", "/v1/inputs", "1000", nonce, body), expected)

    def test_case_insensitive_header_names_and_valid_request(self):
        headers = self.headers()
        self.assertEqual(self.auth.verify("GET", "/v1/health", {k.lower(): v for k, v in headers.items()},
                                          b"", now=1000), headers["X-TM-Nonce"])

    def test_duplicate_case_variant_auth_header_rejected(self):
        headers = self.headers()
        headers["x-tm-project"] = self.project
        self.assertAuthError("invalid_auth", "GET", "/v1/health", headers)

    def test_mutated_body_path_method_and_audience_rejected(self):
        body = b'{"a":1}'
        for method, path, request_body in (("GET", "/v1/inputs", body),
                                          ("POST", "/v1/jobs", body),
                                          ("POST", "/v1/inputs", b'{"a":2}')):
            headers = self.headers("POST", "/v1/inputs", body)
            self.assertAuthError("invalid_auth", method, path, headers, request_body)
        headers = self.headers()
        headers["X-TM-Project"] = "2" * 32
        self.assertAuthError("invalid_auth", "GET", "/v1/health", headers)
        self.assertEqual(self.store.counts()["nonces"], 0)

    def test_bad_signature_does_not_reserve_nonce(self):
        headers = self.headers()
        valid = dict(headers)
        headers["X-TM-Signature"] = "0" * 64
        self.assertAuthError("invalid_auth", "GET", "/v1/health", headers)
        self.assertEqual(self.store.counts()["nonces"], 0)
        self.auth.verify("GET", "/v1/health", valid, b"", now=1000)

    def test_auth_replay_across_restart(self):
        headers = self.headers()
        self.auth.verify("GET", "/v1/health", headers, b"", now=1000)
        with Store(self.root) as reopened:
            self.auth = Authenticator(self.key, self.project, reopened)
            self.assertAuthError("replayed_auth", "GET", "/v1/health", headers)

    def test_timestamp_window_past_and_future(self):
        for timestamp in ("969", "1031", "0"):
            self.assertAuthError("expired_auth", "GET", "/v1/health", self.headers(timestamp=timestamp))
        for timestamp in ("970", "1000", "1030"):
            self.auth.verify("GET", "/v1/health", self.headers(timestamp=timestamp), b"", now=1000)

    def test_invalid_headers_rejected(self):
        for field, value in (("X-TM-Time", "1.0"), ("X-TM-Time", "1000\n"),
                             ("X-TM-Nonce", "a" * 31), ("X-TM-Nonce", "A" * 32),
                             ("X-TM-Signature", "f" * 63), ("X-TM-Project", "invalid")):
            headers = self.headers()
            headers[field] = value
            self.assertAuthError("invalid_auth", "GET", "/v1/health", headers)
        self.assertAuthError("invalid_auth", "GET", "/v1/health", {})

    def test_nonce_ledger_failure_denies_auth(self):
        (self.root / "nonces" / "corrupt").write_text("x")
        self.assertAuthError("auth_unavailable", "GET", "/v1/health", self.headers())

    def test_response_signature_binds_status_nonce_and_exact_body(self):
        signature = response_signature(self.key, "a" * 32, 200, b'{"ok":true}')
        for nonce, status, body in (("b" * 32, 200, b'{"ok":true}'),
                                     ("a" * 32, 401, b'{"ok":true}'),
                                     ("a" * 32, 200, b'{"ok":false}')):
            self.assertNotEqual(signature, response_signature(self.key, nonce, status, body))
        canonical = ("TM1-RESPONSE\n" + "a" * 32 + "\n200\n" + hashlib.sha256(b'{"ok":true}').hexdigest()).encode()
        self.assertEqual(signature, hmac.new(self.key, canonical, hashlib.sha256).hexdigest())

    def test_private_key_loading_and_permissions(self):
        path = self.base / "key"
        path.write_text(self.key.hex() + "\n")
        os.chmod(path, 0o600)
        self.assertEqual(read_private_key(path), self.key)
        os.chmod(path, 0o644)
        self.assertStoreError("unsafe_file", read_private_key, path)

    def test_private_key_symlink_hardlink_and_invalid_contents(self):
        path = self.base / "key"
        path.write_text(self.key.hex())
        os.chmod(path, 0o600)
        link = self.base / "alias"
        link.symlink_to(path)
        self.assertStoreError("unsafe_file", read_private_key, link)
        link.unlink()
        os.link(path, link)
        self.assertStoreError("unsafe_file", read_private_key, path)
        link.unlink()
        path.write_text("g" * 64)
        self.assertStoreError("invalid_key", read_private_key, path)


if __name__ == "__main__":
    unittest.main()
