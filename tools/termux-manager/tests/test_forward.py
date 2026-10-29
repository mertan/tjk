"""Synthetic mechanism checks; no market performance is being measured."""
import copy
import json
import sqlite3
import tempfile
import unittest
import contextlib
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from equity_guard.forward import ForwardJournal, make_check
from forward_research import main

NOW = datetime(2026, 10, 29, 17, 10, tzinfo=timezone.utc)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.sqlite"
        self.journal = ForwardJournal(self.path, clock=lambda: NOW)

    def test_untrained_model_cannot_become_ready_from_attestation(self):
        result = make_check(now=NOW, connection_available=True,
                            history_manifest={"training_ready": True, "retrieval_complete": True})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["predictions"], [])
        self.assertIsNone(result["probability"])
        self.assertIn("TRAINED_FORECAST_MODEL_NOT_IMPLEMENTED", result["reasons"])

    def test_current_afternoon_does_not_backdate_opening_forecast(self):
        event = make_check(now=NOW)
        self.assertIn("OPENING_STRATEGY_WINDOW_CLOSED", event["reasons"])
        self.assertEqual(event["protocol"]["price_max_usd"], 5)
        self.assertEqual(event["generated_at"], "2026-10-29T17:10:00Z")

    def test_stale_and_future_checks_rejected(self):
        for delta in (-61, 1):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.journal.append_check(make_check(now=NOW + timedelta(seconds=delta)))

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            make_check(now=NOW.replace(tzinfo=None))

    def test_scanner_score_cannot_be_stored_as_forecast(self):
        for key, value in (("probability", 0.95), ("predictions", [{"symbol": "FAKE"}]),
                           ("model_trained", True), ("live_forecasting_started", True)):
            event = make_check(now=NOW)
            event[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.append_check(event)

    def test_sqlite_update_and_delete_rejected(self):
        self.journal.append_check(make_check(now=NOW))
        with sqlite3.connect(self.path) as db:
            for statement in ("UPDATE forward_events SET payload='{}'", "DELETE FROM forward_events"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
        self.assertTrue(self.journal.verify())

    def test_concurrent_appends_keep_hash_chain(self):
        event = make_check(now=NOW)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.journal.append_check(event), range(12)))
        self.assertEqual(sorted(r["sequence"] for r in results), list(range(1, 13)))
        self.assertTrue(self.journal.verify())

    def test_mutation_of_input_does_not_change_record(self):
        event = make_check(now=NOW)
        original = copy.deepcopy(event)
        result = self.journal.append_check(event)
        event["protocol"]["price_max_usd"] = 99
        with sqlite3.connect(self.path) as db:
            stored = json.loads(db.execute("SELECT payload FROM forward_events").fetchone()[0])
        self.assertEqual(stored, original)
        self.assertEqual(result["event"], original)

    def test_dst_changes_keep_new_york_strategy_window(self):
        october = make_check(now=datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc))
        november = make_check(now=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
        self.assertNotIn("OPENING_STRATEGY_WINDOW_CLOSED", october["reasons"])
        self.assertNotIn("OPENING_STRATEGY_WINDOW_CLOSED", november["reasons"])

    def test_clock_rollback_rejected(self):
        self.journal.append_check(make_check(now=NOW))
        earlier = NOW - timedelta(seconds=1)
        journal = ForwardJournal(self.path, clock=lambda: earlier)
        with self.assertRaises(ValueError):
            journal.append_check(make_check(now=earlier))

    def test_protocol_changes_cannot_keep_old_identity(self):
        event = make_check(now=NOW)
        event["protocol"]["price_max_usd"] = 99
        with self.assertRaises(ValueError):
            self.journal.append_check(event)

    def test_existing_cli_output_does_not_append_again(self):
        output = Path(self.temp.name) / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", "--journal", str(self.path), "--output", str(output)]), 0)
            self.assertEqual(main(["check", "--journal", str(self.path), "--output", str(output)]), 2)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM forward_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
