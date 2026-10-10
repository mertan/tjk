"""Offline ledger tests. example.test evidence/verifiers are synthetic fixtures."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tjk_commands.store import PredictionStore, SafeStoreError, VerifiedResult


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
CUTOFF = NOW + timedelta(hours=1)
FINAL = CUTOFF + timedelta(hours=2)
CHECKED = FINAL + timedelta(minutes=1)
SOURCE = "synthetic-official"
SOURCE_URL = "https://example.test/verified-result"


def fixture_verifier(payload):
    """Only synthetic fixtures: production must supply an authoritative verifier."""
    if not isinstance(payload, VerifiedResult) or payload.source_url != SOURCE_URL:
        raise ValueError("fixture is not authoritative")
    return payload


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "new-project" / "ledger.sqlite3"
        self.store = PredictionStore(self.path, verifiers={SOURCE: fixture_verifier})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def prediction(self, **changes):
        values = dict(request_id="update:1", scope="chat:10", sport="at", model="model-v1",
                      event_id="synthetic-meeting:race-1", market="winner", closes_at=CUTOFF,
                      selection="runner:4", reasons=(), now=NOW)
        values.update(changes)
        return self.store.record_prediction(**values)

    def result(self, **changes):
        values = dict(sport="at", event_id="synthetic-meeting:race-1", market="winner",
                      closes_at=CUTOFF, status="FINAL", winner="runner:4", final_at=FINAL,
                      source_id=SOURCE, source_url=SOURCE_URL, verified_at=CHECKED)
        values.update(changes)
        return VerifiedResult(**values)

    def record_result(self, **changes):
        return self.store.record_result(SOURCE, self.result(**changes), now=CHECKED)

    def assert_code(self, code, callback):
        with self.assertRaises(SafeStoreError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def assert_same_prediction(self, first, second):
        self.assertEqual({key: value for key, value in first.items() if key != "reused"},
                         {key: value for key, value in second.items() if key != "reused"})

    def test_private_new_directory_and_database(self):
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        with sqlite3.connect(self.path) as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"predictions", "verified_results", "command_receipts"})

    def test_restart_preserves_prediction(self):
        expected = self.prediction()
        self.store.close()
        self.store = PredictionStore(self.path)
        replay = self.prediction()
        self.assert_same_prediction(replay, expected)
        self.assertTrue(replay["reused"])
        self.assertFalse(expected["reused"])

    def test_valid_prediction_is_pending(self):
        recorded = self.prediction()
        self.assertEqual(recorded["decision"], "PREDICTION")
        self.assertFalse(recorded["reused"])
        self.assertEqual(recorded["selection"], "runner:4")
        stats = self.store.stats("chat:10")[0]
        self.assertEqual((stats["total"], stats["pending"], stats["pas"]), (1, 1, 0))
        self.assertIsNone(stats["success_rate"])

    def test_pas_hides_selection_and_never_counts_as_incorrect(self):
        recorded = self.prediction(reasons=("SOURCE_STALE",))
        self.assertEqual(recorded["decision"], "PAS")
        self.assertIsNone(recorded["selection"])
        self.record_result(winner="runner:9")
        stats = self.store.stats("chat:10")[0]
        self.assertEqual((stats["pas"], stats["pending"], stats["incorrect"]), (1, 0, 0))
        self.assertIsNone(stats["success_rate"])

    def test_missing_mandatory_identity_fails_closed(self):
        for field in ("event_id", "market", "closes_at", "selection"):
            with self.subTest(field=field):
                row = self.prediction(request_id=field, **{field: None})
                self.assertEqual(row["decision"], "PAS")
                self.assertIn("MISSING_PREDICTION_DATA", row["reasons"])

    def test_prediction_at_or_after_cutoff_is_pas(self):
        for index, now in enumerate((CUTOFF, FINAL)):
            row = self.prediction(request_id=f"late:{index}", now=now)
            self.assertEqual(row["decision"], "PAS")
            self.assertIn("PREDICTION_CLOSED", row["reasons"])

    def test_stored_result_blocks_backdated_prediction(self):
        self.record_result()
        row = self.prediction(now=NOW)
        self.assertEqual(row["decision"], "PAS")
        self.assertIn("RESULT_ALREADY_RECORDED", row["reasons"])
        self.assertEqual(self.store.stats("chat:10")[0]["correct"], 0)

    def test_repeated_telegram_update_returns_original_without_inflating_counts(self):
        original = self.prediction()
        replay = self.prediction(selection="runner:9", now=FINAL)
        self.assert_same_prediction(replay, original)
        self.assertTrue(replay["reused"])
        self.assertFalse(original["reused"])
        self.assertEqual(self.store.stats("chat:10")[0]["total"], 1)

    def test_request_id_is_scoped_to_chat(self):
        self.prediction()
        self.prediction(scope="chat:20", selection="runner:9")
        self.record_result()
        self.assertEqual(self.store.stats("chat:10")[0]["correct"], 1)
        self.assertEqual(self.store.stats("chat:20")[0]["incorrect"], 1)
        self.assertEqual(self.store.stats("chat:30"), [])

    def test_negative_telegram_group_scope_is_supported(self):
        self.prediction(scope="-1001234567890")
        self.assertEqual(self.store.stats("-1001234567890")[0]["pending"], 1)

    def test_new_commands_for_same_event_model_do_not_weight_performance(self):
        original = self.prediction()
        repeated = self.prediction(request_id="update:2", selection="runner:9")
        replay = self.prediction(request_id="update:2", event_id="changed-provider-answer")
        self.assert_same_prediction(original, repeated)
        self.assert_same_prediction(original, replay)
        self.assertFalse(original["reused"])
        self.assertTrue(repeated["reused"])
        self.assertTrue(replay["reused"])
        self.record_result()
        self.assertEqual(self.store.stats("chat:10")[0]["total"], 1)
        self.assertEqual(self.store.stats("chat:10")[0]["correct"], 1)

    def test_later_closed_command_is_pas_not_recycled_prediction(self):
        original = self.prediction()
        late = self.prediction(request_id="update:2", now=FINAL)
        self.assertEqual(original["decision"], "PREDICTION")
        self.assertEqual(late["decision"], "PAS")
        self.assertEqual(self.store.stats("chat:10")[0]["pas"], 1)

    def test_no_default_result_verifier_and_no_verified_flag_shortcut(self):
        with PredictionStore(Path(self.tmp.name) / "no-verifier" / "ledger.sqlite3") as store:
            self.assert_code("RESULT_VERIFIER_UNAVAILABLE", lambda: store.record_result(
                SOURCE, {"verified": True, "winner": "runner:4"}, now=CHECKED))

    def test_unknown_verifier_is_rejected(self):
        self.assert_code("RESULT_VERIFIER_UNAVAILABLE", lambda: self.store.record_result(
            "unknown", self.result(), now=CHECKED))

    def test_verifier_failure_has_no_payload_or_exception_leak(self):
        secret = "sensitive-raw-provider-payload"
        def broken_verifier(_payload):
            raise RuntimeError(secret)
        with PredictionStore(Path(self.tmp.name) / "broken" / "ledger.sqlite3",
                             verifiers={SOURCE: broken_verifier}) as store:
            self.assert_code("RESULT_VERIFICATION_FAILED", lambda: store.record_result(
                SOURCE, {"private": secret}, now=CHECKED))

    def test_boolean_verification_claim_is_not_a_result(self):
        with PredictionStore(Path(self.tmp.name) / "bool" / "ledger.sqlite3",
                             verifiers={SOURCE: lambda _payload: {"verified": True}}) as store:
            self.assert_code("RESULT_VERIFICATION_FAILED", lambda: store.record_result(
                SOURCE, {}, now=CHECKED))

    def test_correct_incorrect_pas_pending_and_void_are_separate(self):
        self.prediction(request_id="correct")
        self.prediction(request_id="incorrect", event_id="incorrect-race", selection="runner:9")
        self.prediction(request_id="pas", reasons=("DATA_MISSING",))
        self.prediction(request_id="pending", event_id="pending-race")
        self.prediction(request_id="void", event_id="void-race")
        self.record_result()
        self.record_result(event_id="incorrect-race")
        self.record_result(event_id="void-race", status="VOID", winner=None)
        stats = self.store.stats("chat:10")[0]
        self.assertEqual({k: stats[k] for k in ("total", "correct", "incorrect", "pas", "pending", "void")},
                         {"total": 5, "correct": 1, "incorrect": 1, "pas": 1, "pending": 1, "void": 1})
        self.assertEqual(stats["success_rate"], 0.5)

    def test_sport_model_market_and_horizon_are_not_cross_joined(self):
        self.prediction()
        self.prediction(request_id="model-v2", model="model-v2", selection="runner:9")
        self.prediction(request_id="basket", sport="basket")
        self.prediction(request_id="market", market="place")
        self.prediction(request_id="horizon", closes_at=CUTOFF + timedelta(minutes=1))
        self.record_result()
        rows = {(row["sport"], row["model"]): row for row in self.store.stats("chat:10")}
        self.assertEqual(rows[("at", "model-v1")]["correct"], 1)
        self.assertEqual(rows[("at", "model-v1")]["pending"], 2)
        self.assertEqual(rows[("at", "model-v2")]["incorrect"], 1)
        self.assertEqual(rows[("basket", "model-v1")]["pending"], 1)

    def test_exact_duplicate_result_is_idempotent(self):
        original = self.record_result()
        replay = self.store.record_result(SOURCE, self.result(), now=CHECKED + timedelta(hours=1))
        self.assertEqual(original, replay)

    def test_reverified_same_result_preserves_original_evidence_metadata(self):
        original = self.record_result()
        checked_again = CHECKED + timedelta(hours=1)
        replay = self.store.record_result(SOURCE, self.result(verified_at=checked_again), now=checked_again)
        self.assertEqual(original, replay)
        self.assertEqual(replay["verified_at"], CHECKED.isoformat(timespec="microseconds"))

    def test_result_cannot_be_silently_corrected(self):
        self.prediction()
        self.record_result()
        self.assert_code("RESULT_CONFLICT", lambda: self.record_result(winner="runner:9"))
        self.assertEqual(self.store.stats("chat:10")[0]["correct"], 1)

    def test_verifier_source_must_match_registration(self):
        self.assert_code("INVALID_RESULT", lambda: self.record_result(source_id="other-source"))

    def test_result_timestamps_must_be_monotonic_and_not_future(self):
        invalid = ({"closes_at": CHECKED + timedelta(days=1)},
                   {"final_at": CUTOFF - timedelta(seconds=1)},
                   {"verified_at": FINAL - timedelta(seconds=1)},
                   {"verified_at": CHECKED + timedelta(seconds=1)})
        for values in invalid:
            with self.subTest(values=values):
                self.assert_code("INVALID_RESULT", lambda: self.record_result(**values))

    def test_final_and_void_winner_rules(self):
        for values in ({"winner": None}, {"winner": ""}, {"status": "PROVISIONAL"},
                       {"status": "VOID", "winner": "runner:4"}):
            with self.subTest(values=values):
                self.assert_code("INVALID_RESULT", lambda: self.record_result(**values))

    def test_verified_pre_start_cancellation_counts_void_and_blocks_new_prediction(self):
        self.prediction()
        cancelled_at = NOW + timedelta(minutes=1)
        verified_at = NOW + timedelta(minutes=2)
        cancellation = self.result(status="VOID", winner=None, final_at=cancelled_at,
                                   verified_at=verified_at)
        self.store.record_result(SOURCE, cancellation, now=verified_at)
        self.assertEqual(self.store.stats("chat:10")[0]["void"], 1)
        self.assertIsNone(self.store.stats("chat:10")[0]["success_rate"])
        row = self.prediction(request_id="after-cancellation", now=verified_at)
        self.assertEqual(row["decision"], "PAS")
        self.assertIn("RESULT_ALREADY_RECORDED", row["reasons"])

    def test_source_url_requires_https_without_credentials(self):
        for url in ("http://example.test/result", "https://user:password@example.test/result",
                    "https://example.test:8443/result", "https://example.test/result#secret",
                    "https://example.test/result?token=private"):
            with PredictionStore(Path(self.tmp.name) / f"url{len(url)}" / "ledger.sqlite3",
                                 verifiers={SOURCE: lambda payload: payload}) as store:
                self.assert_code("INVALID_RESULT", lambda: store.record_result(
                    SOURCE, self.result(source_url=url), now=CHECKED))

    def test_result_recording_clock_is_sampled_after_verification(self):
        observed = []
        def verifier(payload):
            observed.append("verification")
            return payload
        def clock():
            observed.append("clock")
            return CHECKED
        with PredictionStore(Path(self.tmp.name) / "clock" / "ledger.sqlite3",
                             verifiers={SOURCE: verifier}, clock=clock) as store:
            row = store.record_result(SOURCE, self.result())
        self.assertEqual(observed, ["verification", "clock"])
        self.assertEqual(row["recorded_at"], CHECKED.isoformat(timespec="microseconds"))

    def test_naive_datetimes_are_rejected(self):
        self.assert_code("INVALID_PREDICTION", lambda: self.prediction(now=NOW.replace(tzinfo=None)))
        self.assert_code("INVALID_RESULT", lambda: self.record_result(final_at=FINAL.replace(tzinfo=None)))

    def test_offset_datetimes_normalize_before_matching(self):
        local = timezone(timedelta(hours=3))
        self.prediction(closes_at=CUTOFF.astimezone(local), now=NOW.astimezone(local))
        self.record_result()
        self.assertEqual(self.store.stats("chat:10")[0]["correct"], 1)

    def test_provenance_is_metadata_only_and_delay_is_preserved(self):
        row = self.prediction(provenance=({"name": "quotes", "source_id": "fixture-feed",
            "source_url": "https://example.test/quotes", "as_of": NOW,
            "delay_seconds": 900},))
        self.assertEqual(row["provenance"][0]["delay_seconds"], 900)
        self.assertEqual(row["provenance"][0]["as_of"], NOW.isoformat(timespec="microseconds"))

    def test_provenance_rejects_raw_features_and_future_data(self):
        base = {"source_id": "fixture", "source_url": "https://example.test/feed", "as_of": NOW}
        for value in ({**base, "agf": 99}, {**base, "raw_payload": "private"},
                      {**base, "source_url": "https://example.test/feed?api_key=private"},
                      {**base, "as_of": NOW + timedelta(seconds=1)},
                      {**base, "delay_seconds": True}, {**base, "delay_seconds": -1}):
            self.assert_code("INVALID_PROVENANCE", lambda: self.prediction(provenance=(value,)))
        self.assertEqual(self.store.stats("chat:10"), [])

    def test_parameterized_fields_do_not_execute_sql(self):
        self.assert_code("INVALID_PREDICTION", lambda: self.prediction(model="'; DROP TABLE predictions; --"))
        self.prediction(event_id="synthetic/event:1")
        self.assertEqual(self.store.stats("chat:10")[0]["total"], 1)

    def test_prediction_and_result_rows_are_append_only(self):
        self.prediction()
        self.record_result()
        with sqlite3.connect(self.path) as connection:
            for statement in ("UPDATE predictions SET selection='runner:9'", "DELETE FROM predictions",
                              "UPDATE verified_results SET winner='runner:9'", "DELETE FROM verified_results",
                              "UPDATE command_receipts SET request_id='changed'", "DELETE FROM command_receipts"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)

    def test_two_connections_deduplicate_concurrent_retries(self):
        with PredictionStore(self.path) as second:
            def insert(store):
                return store.record_prediction(request_id="retry", scope="chat:10", sport="at",
                    model="model-v1", event_id="race", market="winner", closes_at=CUTOFF,
                    selection="runner:4", reasons=(), now=NOW)
            with ThreadPoolExecutor(max_workers=2) as executor:
                rows = list(executor.map(insert, (self.store, second)))
        self.assert_same_prediction(rows[0], rows[1])
        self.assertEqual({row["reused"] for row in rows}, {False, True})
        self.assertEqual(self.store.stats("chat:10")[0]["total"], 1)

    def test_concurrent_new_commands_keep_one_event_prediction(self):
        with PredictionStore(self.path) as second:
            def insert(pair):
                store, request_id = pair
                return store.record_prediction(request_id=request_id, scope="chat:10", sport="at",
                    model="model-v1", event_id="race", market="winner", closes_at=CUTOFF,
                    selection="runner:4", reasons=(), now=NOW)
            with ThreadPoolExecutor(max_workers=2) as executor:
                rows = list(executor.map(insert, ((self.store, "first"), (second, "second"))))
        self.assert_same_prediction(rows[0], rows[1])
        self.assertEqual({row["reused"] for row in rows}, {False, True})
        self.assertEqual(self.store.stats("chat:10")[0]["total"], 1)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0], 2)

    def test_unrelated_existing_database_is_not_changed(self):
        other = Path(self.tmp.name) / "existing.sqlite3"
        fd = os.open(other, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(other) as connection:
            connection.execute("CREATE TABLE existing_bot(value TEXT)")
            connection.execute("INSERT INTO existing_bot VALUES ('untouched')")
        original = other.read_bytes()
        self.assert_code("UNRELATED_DATABASE", lambda: PredictionStore(other))
        self.assertEqual(other.read_bytes(), original)

    def test_unprivate_parent_is_rejected_without_chmod(self):
        parent = Path(self.tmp.name) / "public"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        self.assert_code("UNSAFE_DATABASE_PATH", lambda: PredictionStore(parent / "ledger.sqlite3"))
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertFalse((parent / "ledger.sqlite3").exists())

    def test_symlink_database_and_ancestor_are_rejected(self):
        link = Path(self.tmp.name) / "link.sqlite3"
        link.symlink_to(self.path)
        self.assert_code("UNSAFE_DATABASE_PATH", lambda: PredictionStore(link))
        directory_link = Path(self.tmp.name) / "directory-link"
        directory_link.symlink_to(self.path.parent, target_is_directory=True)
        self.assert_code("UNSAFE_DATABASE_PATH", lambda: PredictionStore(directory_link / "other.sqlite3"))
        self.assertFalse((self.path.parent / "other.sqlite3").exists())

    def test_hardlinked_database_is_rejected(self):
        linked = Path(self.tmp.name) / "hardlink.sqlite3"
        os.link(self.path, linked)
        self.assert_code("UNSAFE_DATABASE_PATH", lambda: PredictionStore(linked))

    def test_closed_store_has_fixed_error(self):
        self.store.close()
        self.assert_code("DATABASE_CLOSED", lambda: self.store.stats("chat:10"))


if __name__ == "__main__":
    unittest.main()
