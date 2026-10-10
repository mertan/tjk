"""Startup failures stay useful without exposing local secrets or changing files."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch

from termux_manager.diagnostics import CODES, STAGES, StartupFailure, failure_record
from termux_manager.server import main, make_server
from termux_manager.storage import Store, StoreError


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="private-startup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir(mode=0o700)
        for namespace in ("inputs", "jobs", "results", "nonces"):
            (self.runtime / namespace).mkdir(mode=0o700)
        self.key = self.base / "auth.key"
        self.key_text = "df" * 32
        self.key.write_text(self.key_text + "\n")
        self.key.chmod(0o600)
        self.project = "ab" * 16
        self.config = self.base / "project.json"
        self.config.write_text(json.dumps({"project_id": self.project,
                                           "public_roots": ["/private-account-marker"]}))
        self.config.chmod(0o600)
        self.args = ["--root", str(self.runtime), "--key-file", str(self.key),
                     "--config", str(self.config), "--port", "0"]

    def run_main(self, *extra, args=None):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try:
                code = main([*(self.args if args is None else args), *extra])
            except SystemExit as stopped:
                code = stopped.code
        combined = output.getvalue() + error.getvalue()
        for sensitive in (self.key_text, str(self.base), self.project,
                          "/private-account-marker", "Traceback (most recent call last)"):
            self.assertNotIn(sensitive, combined)
        return code, output.getvalue(), error.getvalue()

    def assert_failure(self, stage, code, *extra, errno_name=None, args=None):
        status, output, error = self.run_main(*extra, args=args)
        self.assertEqual(status, 2 if stage == "arguments" else 1)
        self.assertEqual(output, "")
        self.assertEqual(len(error.splitlines()), 1)
        record = json.loads(error)
        self.assertEqual(record["event"], "manager_start_failed")
        self.assertEqual(record["stage"], stage)
        self.assertEqual(record["code"], code)
        self.assertEqual(record["errno"], errno_name)
        self.assertIs(record["execution_enabled"], False)
        self.assertRegex(record["python"], r"^\d+\.\d+\.\d+$")
        return record

    def snapshot(self):
        result = {}
        for item in [self.base, *self.base.rglob("*")]:
            info = item.lstat()
            result[str(item.relative_to(self.base))] = (
                stat.S_IMODE(info.st_mode),
                item.read_bytes() if item.is_file() else None,
            )
        return result

    def test_missing_key_identifies_authentication_stage_without_path(self):
        self.key.unlink()
        self.assert_failure("authentication_key", "unsafe_file", errno_name="ENOENT")

    def test_invalid_key_contents_are_never_rendered(self):
        self.key.write_text("secret-invalid-key-marker")
        record = self.assert_failure("authentication_key", "invalid_key")
        self.assertNotIn("secret-invalid-key-marker", json.dumps(record))

    def test_key_permission_failure_does_not_repair_existing_file(self):
        self.key.chmod(0o644)
        before = self.snapshot()
        self.assert_failure("authentication_key", "unsafe_file")
        self.assertEqual(self.snapshot(), before)

    def test_missing_runtime_namespace_is_distinct_from_key_failure(self):
        (self.runtime / "nonces").rmdir()
        self.assert_failure("runtime", "unsafe_directory", errno_name="ENOENT")

    def test_missing_runtime_root(self):
        for child in self.runtime.iterdir():
            child.rmdir()
        self.runtime.rmdir()
        self.assert_failure("runtime", "unsafe_directory", errno_name="ENOENT")

    def test_runtime_permission_failure_is_read_only(self):
        self.runtime.chmod(0o755)
        before = self.snapshot()
        self.assert_failure("runtime", "unsafe_directory")
        self.assertEqual(self.snapshot(), before)

    def test_malformed_private_configuration_never_echoes_content(self):
        self.config.write_text('{"private-account-marker": "secret-invalid-config"')
        before = self.snapshot()
        record = self.assert_failure("project_config", "invalid_json")
        self.assertNotIn("secret-invalid-config", json.dumps(record))
        self.assertEqual(self.snapshot(), before)

    def test_missing_configuration_identifies_project_stage(self):
        self.config.unlink()
        self.assert_failure("project_config", "not_found", errno_name="ENOENT")

    def test_unsafe_configuration_permissions_are_not_repaired(self):
        self.config.chmod(0o644)
        before = self.snapshot()
        self.assert_failure("project_config", "unsafe_file")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_or_absent_project_id_is_not_echoed(self):
        for identity in (None, "secret-invalid-project", "a" * 31, 42):
            with self.subTest(identity_type=type(identity).__name__):
                self.config.write_text(json.dumps({"project_id": identity}))
                self.assert_failure("identity", "invalid_project_id")

    def test_explicit_project_id_remains_supported(self):
        arguments = ["--root", str(self.runtime), "--key-file", str(self.key),
                     "--project-id", self.project, "--port", "0", "--check"]
        code, output, error = self.run_main(args=arguments)
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(json.loads(output)["event"], "manager_preflight_ok")

    def test_invalid_bind_and_port_are_separate_from_storage(self):
        self.key.unlink()
        self.assert_failure("bind_config", "invalid_bind", "--bind", "secret-invalid-bind")
        self.assert_failure("bind_config", "invalid_port", "--port", "65536")

    def test_argument_errors_never_echo_arbitrary_argument_values(self):
        for extra in (("--port", "secret-nonnumeric-port"),
                      ("--unexpected-secret-option", "secret-argument-value"),
                      ("--project-id", "secret-conflicting-id")):
            with self.subTest(option=extra[0]):
                record = self.assert_failure("arguments", "invalid_arguments", *extra)
                rendered = json.dumps(record)
                for value in extra:
                    self.assertNotIn(value, rendered)

    def test_check_validates_files_without_lock_socket_or_writes(self):
        before = self.snapshot()
        with patch("termux_manager.server.make_server") as factory, \
                patch("termux_manager.server.socket.socket") as sockets:
            code, output, error = self.run_main("--check")
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(json.loads(output), {
            "event": "manager_preflight_ok", "execution_enabled": False,
            "socket_bound": False, "server_lock_checked": False,
        })
        factory.assert_not_called()
        sockets.assert_not_called()
        self.assertFalse((self.runtime / ".server.lock").exists())
        self.assertEqual(self.snapshot(), before)

    def test_check_does_not_modify_or_claim_to_validate_existing_lock(self):
        lock = self.runtime / ".server.lock"
        lock.write_bytes(b"existing-lock-marker")
        lock.chmod(0o644)
        before = self.snapshot()
        code, output, error = self.run_main("--check")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["server_lock_checked"])
        self.assertEqual(error, "")
        self.assertEqual(self.snapshot(), before)

    def test_already_running_manager_identifies_lifetime_lock(self):
        with Store(self.runtime) as store:
            server = make_server("127.0.0.1", 0, bytes.fromhex(self.key_text), self.project, store)
            try:
                self.assert_failure("server_lock", "manager_already_running", errno_name="EAGAIN")
            finally:
                server.server_close()

    def test_unsafe_server_lock_does_not_change_it(self):
        lock = self.runtime / ".server.lock"
        lock.write_bytes(b"existing-lock-marker")
        lock.chmod(0o644)
        before = self.snapshot()
        self.assert_failure("server_lock", "unsafe_server_lock")
        self.assertEqual(self.snapshot(), before)

    def test_real_occupied_port_reports_bind_errno_and_releases_lock(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            self.assert_failure("socket_bind", "socket_unavailable", "--port",
                                str(occupied.getsockname()[1]), errno_name="EADDRINUSE")
        # The failed constructor must leave the runtime available for a retry.
        with Store(self.runtime) as store:
            server = make_server("127.0.0.1", 0, bytes.fromhex(self.key_text), self.project, store)
            server.server_close()

    def test_real_config_start_and_clean_interrupt_preserve_existing_files(self):
        before = self.snapshot()
        with patch("termux_manager.server.ManagementServer.serve_forever", side_effect=KeyboardInterrupt):
            code, output, error = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Analysis API listening on 127.0.0.1:", output)
        self.assertIn("execution_enabled=false", output)
        self.assertEqual(error, "")
        after = self.snapshot()
        self.assertEqual(set(after) - set(before), {"runtime/.server.lock"})
        for name, value in before.items():
            self.assertEqual(after[name], value)
        with Store(self.runtime) as store:
            server = make_server("127.0.0.1", 0, bytes.fromhex(self.key_text), self.project, store)
            server.server_close()

    def test_unknown_exception_message_cause_and_filename_are_not_rendered(self):
        secret = "secret-account-and-password-marker"
        low = OSError(errno.EACCES, secret, str(self.base / secret))
        high = RuntimeError(secret)
        high.__cause__ = low
        with patch("termux_manager.server.read_private_key", side_effect=high):
            record = self.assert_failure("authentication_key", "unexpected_failure", errno_name="EACCES")
        self.assertNotIn(secret, json.dumps(record))

    def test_unknown_store_code_is_not_a_public_diagnostic(self):
        secret = "secret-unknown-store-code"
        with patch("termux_manager.server.read_private_key", side_effect=StoreError(secret)):
            record = self.assert_failure("authentication_key", "unexpected_failure")
        self.assertNotIn(secret, json.dumps(record))

    def test_cyclic_exception_chain_is_bounded_and_keeps_no_secret(self):
        first = RuntimeError("secret-first-cause")
        second = ValueError("secret-second-cause")
        first.__cause__, second.__cause__ = second, first
        with patch("termux_manager.server.read_private_key", side_effect=first):
            self.assert_failure("authentication_key", "unexpected_failure")

    def test_long_cause_chain_has_bounded_diagnostic_output(self):
        cause = OSError(errno.EACCES, "secret-cause-beyond-bound")
        for _ in range(32):
            error = RuntimeError("secret-chain-link")
            error.__cause__ = cause
            cause = error
        record = failure_record("authentication_key", cause)
        self.assertIsNone(record["errno"])
        self.assertLess(len(json.dumps(record)), 512)
        self.assertNotIn("secret", json.dumps(record))

    def test_unknown_stage_code_and_errno_are_never_echoed(self):
        for error in (StartupFailure("secret-stage", "secret-code"),
                      OSError(99999999, "secret-error-number")):
            with self.subTest(error_type=type(error).__name__):
                record = failure_record("secret-input-stage", error)
                self.assertIn(record["stage"], STAGES)
                self.assertIn(record["code"], CODES)
                self.assertIsNone(record["errno"])
                self.assertNotIn("secret", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
