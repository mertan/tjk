"""Synthetic /proc fixtures only. Never inspect or change other real processes."""
from pathlib import Path
import tempfile
import unittest

from termux_manager.discovery import DiscoveryError, discover_http_roots


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.proc = self.base / "proc"
        self.proc.mkdir()
        self.web = self.base / "web"
        self.web.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def process(self, pid, args, cwd=None):
        process = self.proc / str(pid)
        process.mkdir()
        (process / "comm").write_text("python3\n")
        (process / "cmdline").write_bytes(b"\0".join(x.encode() for x in args) + b"\0")
        (process / "cwd").symlink_to(cwd or self.web, target_is_directory=True)
        return process

    def test_working_directory_detected_without_mutation(self):
        p = self.process(101, ["python3", "-m", "http.server", "8080"])
        before = (p / "cmdline").read_bytes()
        self.assertEqual(discover_http_roots(8080, self.proc), [self.web])
        self.assertEqual((p / "cmdline").read_bytes(), before)

    def test_absolute_and_relative_directory_and_multiple_binds(self):
        child = self.web / "static"
        child.mkdir()
        self.process(102, ["python3", "-m", "http.server", "8080", "--directory", str(child), "--bind", "127.0.0.1"])
        self.process(103, ["python3", "-m", "http.server", "-d", "static", "8080", "-b", "0.0.0.0"])
        self.assertEqual(discover_http_roots(8080, self.proc), [child])

    def test_other_ports_modules_and_custom_scripts_do_not_guess(self):
        self.process(104, ["python3", "-m", "http.server", "8000"])
        self.process(105, ["python3", "-m", "mybot", "8080"])
        self.process(106, ["python3", "custom_server.py", "8080"])
        self.process(112, ["python3", "custom_server.py", "-m", "http.server", "8080"])
        with self.assertRaisesRegex(DiscoveryError, "not_found"):
            discover_http_roots(8080, self.proc)

    def test_unknown_server_options_fail_closed(self):
        self.process(107, ["python3", "-m", "http.server", "8080", "--unknown-root", str(self.web)])
        with self.assertRaises(DiscoveryError):
            discover_http_roots(8080, self.proc)

    def test_missing_root_and_metadata_fail_closed(self):
        self.process(108, ["python3", "-m", "http.server", "8080", "-d", str(self.base / "missing")])
        with self.assertRaises(DiscoveryError):
            discover_http_roots(8080, self.proc)

    def test_symlink_directory_is_canonicalized(self):
        alias = self.base / "alias"
        alias.symlink_to(self.web, target_is_directory=True)
        self.process(109, ["python3", "-m", "http.server", "8080", "-d", str(alias)])
        self.assertEqual(discover_http_roots(8080, self.proc), [self.web])

    def test_directory_cycle_is_a_controlled_discovery_failure(self):
        alias = self.base / "cycle"
        alias.symlink_to(alias, target_is_directory=True)
        self.process(113, ["python3", "-m", "http.server", "8080", "-d", str(alias)])
        with self.assertRaisesRegex(DiscoveryError, "unavailable"):
            discover_http_roots(8080, self.proc)

    def test_multiple_document_roots_on_same_port_all_reported(self):
        second = self.base / "second-web"
        second.mkdir()
        self.process(110, ["python3", "-m", "http.server", "8080"])
        self.process(111, ["python3", "-m", "http.server", "8080", "-d", str(second)])
        self.assertEqual(set(discover_http_roots(8080, self.proc)), {self.web, second})


if __name__ == "__main__":
    unittest.main()
