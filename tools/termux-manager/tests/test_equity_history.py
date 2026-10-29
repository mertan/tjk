"""Synthetic transport tests; no credentials, provider data or financial orders."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from equity_guard.history import download_history
from equity_guard.providers import ProviderError

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 17, 0, tzinfo=UTC)
ENV = {"ALPACA_API_KEY_ID": "synthetic-key", "ALPACA_API_SECRET_KEY": "synthetic-secret"}
START = "2026-10-27T13:30:00Z"
END = "2026-10-27T13:32:00Z"


def bar(stamp=START, price=6):
    return {"t": stamp, "o": price, "h": price + 0.1, "l": price - 0.1,
            "c": price, "v": 100, "vw": price, "n": 4}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.destination = Path(self.tmp.name) / "history"
        self.calls = []

    def download(self, getter, **kwargs):
        return download_history(["TESTX"], self.destination, now=NOW,
            start=START, end=END, getter=getter, environ=ENV, **kwargs)

    def transport(self, url, headers, params):
        self.calls.append((url, dict(headers), dict(params)))
        return {"bars": {"TESTX": [bar()]}, "next_page_token": None}

    def test_missing_or_mixed_credentials_never_call_transport_or_create_output(self):
        for env in ({}, {"ALPACA_API_KEY_ID": "one", "APCA_API_SECRET_KEY": "other"}):
            result = download_history(["TESTX"], self.destination, now=NOW,
                start=START, end=END, getter=self.transport, environ=env)
            self.assertEqual(["credentials_missing"], result["errors"])
            self.assertFalse(result["complete"])
        self.assertEqual([], self.calls)
        self.assertFalse(self.destination.exists())

    def test_raw_sip_request_csv_hash_and_training_gates(self):
        result = self.download(self.transport)
        self.assertTrue(result["complete"])
        self.assertTrue(result["retrieval_complete"])
        self.assertFalse(result["training_ready"])
        self.assertFalse(result["model_training_ready"])
        self.assertFalse(result["pit_universe_verified"])
        self.assertFalse(result["expected_bar_coverage_verified"])
        url, _, query = self.calls[0]
        self.assertEqual("https://data.alpaca.markets/v2/stocks/bars", url)
        self.assertEqual(("sip", "raw", "1Min", 10000),
            (query["feed"], query["adjustment"], query["timeframe"], query["limit"]))
        chunk = result["chunks"][0]
        path = self.destination / chunk["file"]
        self.assertEqual(chunk["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual("6", rows[0]["open"])  # no today's $1-5 selection bias
        self.assertEqual(result, json.loads((self.destination / "manifest.json").read_text()))
        self.assertNotIn("synthetic-key", json.dumps(result))
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_pagination_preserves_both_pages_and_tokens_not_in_manifest(self):
        def getter(url, headers, params):
            self.calls.append(dict(params))
            if "page_token" not in params:
                return {"bars": {"TESTX": [bar()]}, "next_page_token": "private-token"}
            return {"bars": {"TESTX": [bar("2026-10-27T13:31:00Z")]}, "next_page_token": None}
        result = self.download(getter)
        self.assertTrue(result["complete"])
        self.assertEqual(2, result["rows"])
        self.assertEqual("private-token", self.calls[1]["page_token"])
        self.assertNotIn("private-token", json.dumps(result))

    def test_pagination_cycle_and_page_limit_fail_closed(self):
        count = 0
        def getter(url, headers, params):
            nonlocal count
            count += 1
            stamp = START if count == 1 else "2026-10-27T13:31:00Z"
            return {"bars": {"TESTX": [bar(stamp)]}, "next_page_token": "same"}
        result = self.download(getter)
        self.assertEqual(["pagination_invalid"], result["errors"])
        self.assertFalse(result["complete"])
        self.destination = Path(self.tmp.name) / "limited"
        result = self.download(getter, max_pages_per_chunk=1)
        self.assertEqual(["pagination_limit_reached"], result["errors"])

    def test_bad_ohlc_duplicate_out_of_range_or_oversized_page_rejected(self):
        cases = [[{**bar(), "l": 9}], [bar(END)], [bar(), bar()],
                 [bar()] * 10001, [{**bar(), "v": True}], [{**bar(), "o": float("nan")}]]
        for i, batch in enumerate(cases):
            with self.subTest(case=i):
                self.destination = Path(self.tmp.name) / str(i)
                result = self.download(lambda *args: {"bars": {"TESTX": batch}})
                self.assertFalse(result["complete"])
                self.assertTrue(result["errors"])

    def test_empty_or_foreign_symbol_chunk_is_not_complete(self):
        for i, payload in enumerate(({"bars": {}}, {"bars": {"OTHER": [bar()]}})):
            self.destination = Path(self.tmp.name) / str(i)
            result = self.download(lambda *args: payload)
            self.assertFalse(result["complete"])
            self.assertIn(result["errors"][0], {"empty_chunk_unverified", "bars_schema_invalid"})

    def test_monthly_boundaries_and_request_budget(self):
        def getter(url, headers, params):
            self.calls.append(dict(params))
            return {"bars": {"TESTX": [bar(params["start"])]}}
        result = download_history(["TESTX"], self.destination, now=NOW,
            start="2026-08-31T00:00:00Z", end="2026-10-01T00:00:00Z",
            environ=ENV, getter=getter)
        self.assertTrue(result["complete"])
        self.assertEqual(["2026-08-31T00:00:00Z", "2026-09-01T00:00:00Z"],
            [c["start"] for c in self.calls])
        self.destination = Path(self.tmp.name) / "budget"
        result = download_history(["TESTX"], self.destination, now=NOW,
            start="2026-08-31T00:00:00Z", end="2026-10-01T00:00:00Z",
            environ=ENV, getter=getter, max_requests=1)
        self.assertFalse(result["complete"])
        self.assertEqual(["request_budget_exceeded"], result["errors"])
        self.assertEqual(1, result["requests"])

    def test_transport_errors_are_scrubbed_and_existing_exports_preserved(self):
        for i, error in enumerate((RuntimeError("synthetic-secret"), ProviderError("synthetic-key"), ProviderError("http_403"))):
            self.destination = Path(self.tmp.name) / str(i)
            def getter(*args):
                raise error
            result = self.download(getter)
            self.assertFalse(result["complete"])
            self.assertNotIn("synthetic", json.dumps(result))
        self.destination = Path(self.tmp.name) / "existing"
        self.destination.mkdir()
        sentinel = self.destination / "keep.txt"
        sentinel.write_text("keep")
        result = self.download(self.transport)
        self.assertEqual(["output_unavailable"], result["errors"])
        self.assertEqual("keep", sentinel.read_text())
        self.assertEqual([], self.calls)

    def test_universe_and_future_cutoff_validation_precedes_network(self):
        for symbols in ([], ["TESTX"] * 2, ["../SECRET"], ["T" + str(i) for i in range(21)]):
            result = download_history(symbols, self.destination, start=START, end=END,
                now=NOW, environ=ENV, getter=self.transport)
            self.assertEqual(["invalid_request"], result["errors"])
        result = download_history(["TESTX"], self.destination, start=START,
            end="2026-10-30T00:00:00Z", now=NOW, environ=ENV, getter=self.transport)
        self.assertEqual(["invalid_request"], result["errors"])
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
