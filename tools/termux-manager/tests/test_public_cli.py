"""CLI safety tests use only synthetic local inputs and a stub adapter."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from equity_guard import public_cli


class PublicCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.report = {
            "decision": "PAS", "data_status": "DATA_UNAVAILABLE",
            "execution_enabled": False,
        }

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, arguments, **stub):
        output = io.StringIO()
        with patch.object(public_cli, "_scan", **stub) as scan:
            with redirect_stdout(output):
                status = public_cli.main(arguments)
        return status, output.getvalue(), scan

    def file(self, value, name="observations.json"):
        path = self.root / name
        path.write_text(value, encoding="utf-8")
        return str(path)

    def assert_error(self, arguments, code):
        status, output, scan = self.invoke(arguments, return_value=self.report)
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output), {
            **self.report, "error": code,
        })
        scan.assert_not_called()
        self.assertNotIn(str(self.root), output)
        return output

    def test_no_arguments_returns_pas_success_without_files(self):
        with patch.dict(os.environ, {}, clear=True):
            status, output, scan = self.invoke([], return_value=self.report)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), self.report)
        scan.assert_called_once_with(
            observations=None, context=None, environ={"SEC_USER_AGENT": ""},
        )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_sec_user_agent_forwarded_never_auth_or_alpaca(self):
        environment = {
            "SEC_USER_AGENT": "Example Analyst example@example.com",
            "TM_AUTH_KEY_HEX": "synthetic_secret_not_for_output",
            "ALPACA_API_SECRET_KEY": "synthetic_alpaca_secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            status, output, scan = self.invoke([], return_value=self.report)
        self.assertEqual(status, 0)
        self.assertEqual(scan.call_args.kwargs["environ"], {
            "SEC_USER_AGENT": environment["SEC_USER_AGENT"],
        })
        self.assertNotIn("synthetic_", output)
        self.assertNotIn("example@example.com", output)

    def test_files_are_read_without_changes(self):
        observations = self.file('{"observations": []}')
        context = self.file('{"cash_try": "50000"}', "context.json")
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        status, _, scan = self.invoke(
            ["--observations", observations, "--context", context],
            return_value=self.report,
        )
        self.assertEqual(status, 0)
        self.assertEqual(scan.call_args.kwargs["observations"], {"observations": []})
        self.assertEqual(scan.call_args.kwargs["context"], {"cash_try": "50000"})
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_unrecognized_arguments_do_not_echo_supplied_value(self):
        output = self.assert_error(["--synthetic_secret"], "invalid_arguments")
        self.assertNotIn("synthetic_secret", output)

    def test_missing_or_repeated_arguments_fail(self):
        for args in (["--context"], ["--help", "--context", "x"],
                     ["--context", "x", "--context", "y"],
                     ["--context", "--observations", "x"]):
            with self.subTest(args=args):
                self.assert_error(args, "invalid_arguments")

    def test_help_is_static(self):
        status, output, scan = self.invoke(["--help"], return_value=self.report)
        self.assertEqual(status, 0)
        self.assertTrue(output.startswith("Usage: python3 -I -B public_scan.py"))
        self.assertIn("--ranking-input FILE | --symbols AAA,BBB", output)
        self.assertIn("missing selection returns PAS", output)
        scan.assert_not_called()

    def test_fintable_and_explicit_symbols_use_isolated_adapter(self):
        status, output, scan = self.invoke(
            ["--provider", "fintable", "--symbols", "AAA,BBB"], return_value=self.report)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), self.report)
        self.assertEqual(scan.call_args.kwargs["provider"], "fintable")
        self.assertEqual(scan.call_args.kwargs["symbols"], ["AAA", "BBB"])
        self.assertIsNone(scan.call_args.kwargs["observations"])

    def test_fintable_rejects_mixed_sources_and_invalid_selection_before_io(self):
        for args in (["--symbols", "AAA"], ["--provider", "other"],
                     ["--provider", "fintable", "--context", "not_read"],
                     ["--provider", "fintable", "--observations", "not_read"],
                     ["--provider", "fintable", "--symbols", "AAA,AAA"],
                     ["--provider", "fintable", "--symbols", "https://example.com"],
                     ["--provider", "fintable", "--symbols", "AAA,,BBB"],
                     ["--provider", "fintable", "--symbols", ",".join("S" + str(i) for i in range(21))]):
            with self.subTest(args=args):
                self.assert_error(args, "invalid_arguments")

    def test_fintable_without_selection_does_not_supply_alphabetical_symbols(self):
        status, output, scan = self.invoke(
            ["--provider", "fintable"], return_value={
                **self.report, "error": "RANKING_INPUT_REQUIRED",
            })
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["error"], "RANKING_INPUT_REQUIRED")
        self.assertEqual(scan.call_args.kwargs["provider"], "fintable")
        self.assertIsNone(scan.call_args.kwargs["symbols"])
        self.assertNotIn("ranking_input", scan.call_args.kwargs)

    def test_ranking_input_is_bounded_read_only_and_forwarded_without_path(self):
        payload = {"schema_version": 1, "source": {}, "records": []}
        filename = self.file(json.dumps(payload), "ranking.json")
        before = Path(filename).read_bytes()
        status, output, scan = self.invoke(
            ["--provider", "fintable", "--ranking-input", filename],
            return_value=self.report,
        )
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), self.report)
        self.assertEqual(scan.call_args.kwargs["ranking_input"], payload)
        self.assertEqual(scan.call_args.kwargs["provider"], "fintable")
        self.assertIsNone(scan.call_args.kwargs["symbols"])
        self.assertNotIn(filename, repr(scan.call_args.kwargs))
        self.assertNotIn(str(self.root), output)
        self.assertEqual(Path(filename).read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir()], ["ranking.json"])

    def test_ranking_conflicting_or_missing_arguments_rejected_before_read(self):
        cases = (
            ["--ranking-input", "not_read"],
            ["--provider", "fintable", "--ranking-input"],
            ["--provider", "fintable", "--ranking-input", "--symbols", "AAA"],
            ["--provider", "fintable", "--ranking-input", "a", "--ranking-input", "b"],
            ["--provider", "fintable", "--ranking-input", "not_read", "--symbols", "AAA"],
            ["--provider", "fintable", "--ranking-input", "not_read", "--observations", "not_read"],
            ["--provider", "fintable", "--ranking-input", "not_read", "--context", "not_read"],
        )
        with patch.object(public_cli, "_read_json") as reader:
            for args in cases:
                with self.subTest(args=args):
                    self.assert_error(args, "invalid_arguments")
            reader.assert_not_called()

    def test_ranking_file_failures_are_safe_and_skip_adapter(self):
        missing = str(self.root / "synthetic_secret_missing")
        invalid = self.file("synthetic_secret_not_json", "invalid.json")
        non_object = self.file("[]", "list.json")
        duplicate = self.file('{"x":1,"x":2}', "duplicate.json")
        nonfinite = self.file('{"x":NaN}', "nonfinite.json")
        deep = self.file('{"x":' * 22 + "null" + "}" * 22, "deep.json")
        cases = (
            (missing, "unsafe_input_file"),
            (str(self.root), "unsafe_input_file"),
            (invalid, "invalid_json"),
            (non_object, "invalid_input_shape"),
            (duplicate, "invalid_json"),
            (nonfinite, "invalid_json"),
            (deep, "input_too_complex"),
        )
        for filename, error in cases:
            with self.subTest(error=error, filename=Path(filename).name):
                output = self.assert_error(
                    ["--provider", "fintable", "--ranking-input", filename], error,
                )
                self.assertNotIn("synthetic_secret", output)

    def test_ranking_symlink_and_fifo_are_rejected_without_following_or_blocking(self):
        target = self.file("{}", "ranking.json")
        link = self.root / "link.json"
        link.symlink_to(target)
        folder = self.root / "parent_link"
        folder.symlink_to(self.root, target_is_directory=True)
        fifo = self.root / "ranking.fifo"
        os.mkfifo(fifo)
        for filename in (link, folder / "ranking.json", fifo):
            with self.subTest(filename=filename.name):
                self.assert_error(
                    ["--provider", "fintable", "--ranking-input", str(filename)],
                    "unsafe_input_file",
                )

    def test_ranking_oversized_file_is_rejected_before_adapter(self):
        path = self.root / "ranking.json"
        with path.open("wb") as stream:
            stream.truncate(public_cli.MAX_INPUT_BYTES + 1)
        self.assert_error(
            ["--provider", "fintable", "--ranking-input", str(path)], "input_too_large",
        )

    def test_ranking_input_node_limit_is_enforced(self):
        filename = self.file(json.dumps({"rows": [None] * 100}), "ranking.json")
        with patch.object(public_cli, "MAX_NODES", 100):
            self.assert_error(
                ["--provider", "fintable", "--ranking-input", filename], "input_too_complex",
            )

    def test_isolated_entrypoint_ignores_pythonpath_without_writes(self):
        rogue = self.root / "equity_guard"
        rogue.mkdir()
        (rogue / "__init__.py").write_text(
            "raise RuntimeError('synthetic_secret_from_pythonpath')\n"
        )
        before = sorted(str(path) for path in self.root.rglob("*"))
        launcher = Path(public_cli.__file__).resolve().parent.parent / "public_scan.py"
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(launcher), "--help"],
            env={"PYTHONPATH": str(self.root)}, cwd=self.root,
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("Usage: python3 -I -B public_scan.py"))
        self.assertEqual(result.stderr, "")
        self.assertEqual(before, sorted(str(path) for path in self.root.rglob("*")))

    def test_missing_file_path_is_not_reported(self):
        output = self.assert_error(
            ["--observations", str(self.root / "synthetic_secret")],
            "unsafe_input_file",
        )
        self.assertNotIn("synthetic_secret", output)

    def test_symlink_file_is_rejected(self):
        target = self.file("{}")
        link = self.root / "link.json"
        link.symlink_to(target)
        self.assert_error(["--observations", str(link)], "unsafe_input_file")

    def test_symlink_parent_is_rejected(self):
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "input.json").write_text("{}")
        link = self.root / "link"
        link.symlink_to(folder, target_is_directory=True)
        self.assert_error(
            ["--observations", str(link / "input.json")], "unsafe_input_file",
        )

    def test_directory_is_rejected(self):
        self.assert_error(["--context", str(self.root)], "unsafe_input_file")

    def test_fifo_is_rejected_without_blocking(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        self.assert_error(["--observations", str(fifo)], "unsafe_input_file")

    def test_size_limit(self):
        path = self.root / "oversized.json"
        with path.open("wb") as stream:
            stream.truncate(public_cli.MAX_INPUT_BYTES + 1)
        self.assert_error(["--observations", str(path)], "input_too_large")

    def test_invalid_json_does_not_echo_file_content(self):
        path = self.file("synthetic_secret_not_json")
        output = self.assert_error(["--observations", path], "invalid_json")
        self.assertNotIn("synthetic_secret", output)

    def test_non_object_input(self):
        for value in ("[]", "null", '"synthetic_secret"', "5"):
            with self.subTest(value=value):
                self.assert_error(["--context", self.file(value)], "invalid_input_shape")

    def test_duplicate_keys_are_rejected(self):
        path = self.file('{"price": 1, "price": 2}')
        self.assert_error(["--observations", path], "invalid_json")

    def test_nonfinite_numbers_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(value=value):
                path = self.file('{"price": ' + value + "}")
                self.assert_error(["--observations", path], "invalid_json")

    def test_invalid_utf8(self):
        path = self.root / "input.json"
        path.write_bytes(b"\xff\xfe")
        self.assert_error(["--observations", str(path)], "invalid_json")

    def test_depth_limit(self):
        path = self.file('{"x":' * 22 + "null" + "}" * 22)
        self.assert_error(["--context", path], "input_too_complex")

    def test_extreme_depth_is_safe(self):
        path = self.file('{"x":' * 1500 + "null" + "}" * 1500)
        status, output, scan = self.invoke(
            ["--context", path], return_value=self.report,
        )
        self.assertEqual(status, 2)
        # CPython versions differ in the parser's own recursion boundary.
        self.assertIn(json.loads(output)["error"], ("invalid_json", "input_too_complex"))
        scan.assert_not_called()

    def test_node_limit(self):
        path = self.file(json.dumps({"items": [None] * 100}))
        with patch.object(public_cli, "MAX_NODES", 100):
            self.assert_error(["--context", path], "input_too_complex")

    def test_unexpected_scan_exception_is_sanitized(self):
        status, output, _ = self.invoke(
            [], side_effect=RuntimeError("synthetic_secret " + str(self.root)),
        )
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output)["error"], "scan_failed")
        self.assertNotIn("synthetic_secret", output)
        self.assertNotIn(str(self.root), output)

    def test_non_json_result_is_sanitized(self):
        for result in ({"secret": float("nan")}, ["synthetic_secret"]):
            with self.subTest(result=result):
                status, output, _ = self.invoke([], return_value=result)
                self.assertEqual(status, 2)
                self.assertEqual(json.loads(output)["error"], "scan_failed")
                self.assertNotIn("secret", output)


if __name__ == "__main__":
    unittest.main()
