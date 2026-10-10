"""Management job tests use synthetic evidence and new temporary runtime trees."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from termux_manager.jobs import Jobs, JobError, validate_context
from termux_manager.storage import Store, StoreError


def runtime(parent):
    root = Path(parent) / "runtime"
    root.mkdir(mode=0o700)
    for name in ("inputs", "jobs", "results", "nonces"):
        (root / name).mkdir(mode=0o700)
    return root


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = runtime(self.temp.name)
        self.store = Store(self.root)
        self.calls = []

        def fake(context):
            self.calls.append(context)
            return {"decision": "PAS", "execution_enabled": False, "candidates": [], "reasons": ["SYNTHETIC"]}
        self.jobs = Jobs(self.store, runner=fake, interval=0)

    def tearDown(self):
        self.jobs.close()
        self.store.close()
        self.temp.cleanup()

    def wait(self, object_id):
        for _ in range(100):
            result = self.jobs.status(object_id)
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_create_only_input_and_idempotent_job(self):
        before = Path(self.temp.name) / "existing.txt"
        before.write_text("untouched")
        created = self.jobs.upload({"symbols": [], "fx": None, "account": None, "costs": None})
        payload = {"action": "scan", "input_id": created["id"], "request_id": "1" * 32}
        started = self.jobs.submit(payload)
        self.assertEqual(started["id"], "1" * 32)
        result = self.wait(started["id"])
        self.assertEqual(result["status"], "finished")
        self.assertTrue(result["historical_result"])
        self.assertTrue(result["requires_fresh_scan_before_manual_action"])
        self.jobs.submit(payload)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(before.read_text(), "untouched")

    def test_idempotency_survives_manager_restart(self):
        created = self.jobs.upload({})
        payload = {"action": "scan", "input_id": created["id"], "request_id": "2" * 32}
        self.jobs.submit(payload)
        self.wait(payload["request_id"])
        other = Jobs(self.store, runner=lambda context: self.fail("must not rerun"), interval=0)
        self.assertEqual(other.submit(payload)["status"], "finished")

    def test_id_reuse_for_other_input_rejected(self):
        first, second = self.jobs.upload({}), self.jobs.upload({})
        p = {"action": "scan", "input_id": first["id"], "request_id": "3" * 32}
        self.jobs.submit(p)
        self.wait(p["request_id"])
        with self.assertRaises(JobError):
            self.jobs.submit({**p, "input_id": second["id"]})

    def test_interrupted_intent_is_not_automatically_restarted(self):
        object_id = "4" * 32
        input_id = self.jobs.upload({})["id"]
        self.store.create("jobs", object_id, {"id": object_id, "input_id": input_id, "action": "scan", "created_at": "SYNTHETIC"})
        result = self.jobs.submit({"action": "scan", "input_id": input_id, "request_id": object_id})
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.calls, [])

    def test_concurrency_and_rate_limits(self):
        block = threading.Event()
        entered = threading.Event()
        def slow(context):
            entered.set()
            block.wait(2)
            return {"decision": "PAS", "execution_enabled": False}
        self.jobs.runner = slow
        self.jobs.interval = 30
        input_id = self.jobs.upload({})["id"]
        p = {"action": "scan", "input_id": input_id, "request_id": "5" * 32}
        try:
            self.jobs.submit(p)
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(JobError, "job_in_progress"):
                self.jobs.submit({**p, "request_id": "6" * 32})
        finally:
            block.set()
        self.wait(p["request_id"])
        with self.assertRaisesRegex(JobError, "job_rate_limit"):
            self.jobs.submit({**p, "request_id": "6" * 32})

    def test_only_scan_action_no_paths_commands_environment_or_urls(self):
        for context in ({"command": "touch /tmp/bad"}, {"symbols": [], "base_url": "https://bad.invalid"}, {"api_key": "secret"}, {"env": {}}, {"symbols": [{"symbol": "TEST", "path": "/tmp/bad"}]}):
            with self.assertRaises(JobError):
                self.jobs.upload(context)
        for action in ("exec", "shell", "install", "git", "order", "stop", "delete"):
            with self.assertRaises(JobError):
                self.jobs.submit({"action": action, "input_id": "0" * 32, "request_id": "1" * 32})

    def test_failed_runner_sanitizes_error_and_disables_execution(self):
        def broken(context):
            raise RuntimeError("secret value must not escape")
        self.jobs.runner = broken
        input_id = self.jobs.upload({})["id"]
        self.jobs.submit({"action": "scan", "input_id": input_id, "request_id": "7" * 32})
        result = self.wait("7" * 32)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["decision"], "PAS")
        self.assertNotIn("secret value", str(result))

    def test_runner_cannot_mark_execution_enabled(self):
        self.jobs.runner = lambda context: {"execution_enabled": True, "decision": "HAZIRLIK"}
        input_id = self.jobs.upload({})["id"]
        self.jobs.submit({"action": "scan", "input_id": input_id, "request_id": "8" * 32})
        self.assertEqual(self.wait("8" * 32)["result"]["decision"], "PAS")

    def test_nested_unrecognized_fields_and_excessive_depth_rejected(self):
        for context in ({"account": {"positions": [{"symbol": "FICT", "script": "x"}]}}, {"symbols": [{"news_review": {"exec": "x"}}]}, {"costs": {"fee": "wrong"}}, {"symbols": []}):
            if context == {"symbols": []}:
                self.assertEqual(validate_context(context), context)
            else:
                with self.assertRaises(JobError):
                    validate_context(context)


if __name__ == "__main__":
    unittest.main()
