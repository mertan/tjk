"""Install and run the actual isolated CLI; all data is empty/synthetic, local only."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class InstalledFlowTests(unittest.TestCase):
    def test_new_install_cli_signed_upload_scan_result_and_reinstall_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            web = parent / "existing-web"
            web.mkdir()
            old = web / "existing.txt"
            old.write_text("unchanged")
            destination = parent / "new-manager"
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "ALPACA_API_KEY_ID": "", "ALPACA_API_SECRET_KEY": ""}
            def command(args, cwd=SOURCE):
                return subprocess.run([sys.executable, "-I", str(cwd / "launch.py"), *args], cwd=cwd,
                                      env=env, capture_output=True, text=True, timeout=20)
            args = ["install", "--dest", str(destination), "--public-root", str(web)]
            installed = command(args)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            key_before = (destination / "auth.key").read_bytes()
            self.assertNotIn(key_before.decode().strip(), installed.stdout + installed.stderr)
            repeated = command(args)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual((destination / "auth.key").read_bytes(), key_before)
            project = json.loads((destination / "project.json").read_text())["project_id"]
            process = subprocess.Popen([sys.executable, "-I", str(destination / "launch.py"), "serve",
                                        "--root", str(destination / "runtime"), "--key-file", str(destination / "auth.key"),
                                        "--project-id", project, "--port", "0"], cwd=destination, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                line = process.stdout.readline()
                found = re.search(r"127\.0\.0\.1:(\d+)", line)
                self.assertIsNotNone(found, line)
                prefix = ["client", "--url", "http://127.0.0.1:" + found.group(1), "--key-file", str(destination / "auth.key"),
                          "--config", str(destination / "project.json"), "--bypass-proxy"]
                def client(*arguments):
                    result = command(prefix + list(arguments), cwd=destination)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(key_before.decode().strip(), result.stdout + result.stderr)
                    return json.loads(result.stdout)
                self.assertTrue(client("health")["ok"])
                uploaded = client("upload", "--file", str(destination / "context.example.json"))
                requested = client("scan", "--input-id", uploaded["id"], "--request-id", "a" * 32)
                for _ in range(20):
                    result = client("job", "--id", requested["id"])
                    if result["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(result["status"], "finished")
                self.assertEqual(result["result"]["decision"], "PAS")
                self.assertFalse(result["execution_enabled"])
                self.assertEqual(result["result"]["candidates"], [])
                self.assertEqual(client("get-input", "--id", uploaded["id"])["context"]["symbols"], [])
                self.assertEqual(client("status")["counts"]["jobs"], 1)
                self.assertEqual(old.read_text(), "unchanged")
                self.assertEqual(list(web.iterdir()), [old])
            finally:
                process.send_signal(signal.SIGINT)
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
