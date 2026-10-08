"""Synthetic Fintable schema and bounded transport fixtures; no live network."""

import copy
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from equity_guard.fintable import BASE_URL, FintableSource
from equity_guard.public_sources import PublicSourceError, PublicTransport, allowed_url, _pace_fintable

NOW = datetime(2026, 10, 8, 14, tzinfo=timezone.utc)
QUOTE = {"symbol": "TESTX", "price": "2.3100", "currency": "USD", "previous_close": "2.0",
         "volume": 123000, "trading_day": "2026-10-08", "as_of": "2026-10-08T13:59:55Z", "feed": "iex"}
BAR = {"timestamp": "2026-10-08T13:50:00Z", "date": "2026-10-08", "open": "2.0",
       "high": "2.4", "low": "1.9", "close": "2.3", "volume": 1000, "trade_count": 90, "vwap": "2.1"}
HISTORY = {"symbol": "TESTX", "timeframe": "5min", "currency": "USD", "feed": "iex", "bars": [BAR]}
PRICE_URL = BASE_URL + "?symbols=TESTX"
HISTORY_URL = BASE_URL + "/TESTX/history?timeframe=5min&start=2026-10-08&end=2026-10-09&limit=1000"


class Response(io.BytesIO):
    status = 200
    headers = {}


class FixtureTransport:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, headers))
        if isinstance(self.data, Exception):
            raise self.data
        if isinstance(self.data, bytes):
            return self.data
        return json.dumps({"data": self.data}).encode()


def source(data):
    return FintableSource(transport=FixtureTransport(copy.deepcopy(data)), now=NOW)


class FintableTransportTests(unittest.TestCase):
    def test_exact_documented_routes_only(self):
        for url in (PRICE_URL, BASE_URL + "?symbols=TESTX,TESTY", HISTORY_URL,
                    HISTORY_URL.replace("end=2026-10-09", "end=2026-10-08")):
            self.assertTrue(allowed_url(url), url)
        invalid = [BASE_URL, PRICE_URL + "&token=secret", PRICE_URL + "#fragment",
                   PRICE_URL.replace("https", "http"), PRICE_URL.replace("fintable.io", "fintable.io.evil.test"),
                   PRICE_URL.replace("fintable.io", "secret@fintable.io"), PRICE_URL.replace("fintable.io", "fintable.io:444"),
                   PRICE_URL.replace("TESTX", "TESTX%2CTESTY"), BASE_URL + "?symbols=TESTX,TESTX",
                   BASE_URL + "?symbols=" + ",".join("TEST" + str(i) for i in range(21)),
                   HISTORY_URL.replace("5min", "1min"), HISTORY_URL.replace("limit=1000", "limit=1001"),
                   HISTORY_URL.replace("end=2026-10-09", "end=2026-10-10"),
                   HISTORY_URL.replace("end=2026-10-09", "end=2026-10-07"),
                   HISTORY_URL.replace("start=2026-10-08", "start=2026-02-30"),
                   HISTORY_URL.replace("/TESTX/", "/../TESTX/"), PRICE_URL + "&symbols=TESTY",
                   "https://fintable.io/api/v2/orders?symbols=TESTX"]
        opener = Mock()
        transport = PublicTransport(opener=opener)
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url))
                with self.assertRaisesRegex(PublicSourceError, "^endpoint_rejected$"):
                    transport.get(url)
        opener.open.assert_not_called()

    def test_fintable_uses_same_get_only_bounded_transport_and_pacer(self):
        opener = Mock()
        opener.open.return_value = Response(b'{"data": []}')
        transport = PublicTransport(opener=opener)
        with patch("equity_guard.public_sources._pace_fintable") as pace:
            transport.get(PRICE_URL)
        pace.assert_called_once_with(transport.deadline)
        request = opener.open.call_args.args[0]
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertLessEqual(opener.open.call_args.kwargs["timeout"], 12)

    def test_global_pacer_waits_one_second_and_respects_collection_budget(self):
        clock = [100.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch("equity_guard.public_sources._FINTABLE_NEXT_REQUEST", 0.0), \
                patch("equity_guard.public_sources.time.monotonic", side_effect=lambda: clock[0]), \
                patch("equity_guard.public_sources.time.sleep", side_effect=sleep) as wait:
            _pace_fintable(110)
            _pace_fintable(110)
            wait.assert_called_once_with(1.0)
            with self.assertRaisesRegex(PublicSourceError, "^collection_budget_exceeded$"):
                _pace_fintable(101.5)
            self.assertEqual(1, wait.call_count)

    def test_forbidden_and_throttled_http_stop_without_retry(self):
        for status in (403, 429):
            opener = Mock()
            opener.open.side_effect = HTTPError(PRICE_URL, status, "synthetic secret", {}, io.BytesIO())
            provider = FintableSource(PublicTransport(opener=opener), NOW)
            with patch("equity_guard.public_sources._pace_fintable"):
                self.assertEqual(["http_" + str(status)], provider.prices(["TESTX"])["errors"])
                self.assertEqual(["source_access_stopped"], provider.history("TESTX", "2026-10-08")["errors"])
            self.assertEqual(1, opener.open.call_count)


class FintablePriceTests(unittest.TestCase):
    def test_preserves_precision_timestamps_feed_and_unknown_latency(self):
        provider = source([QUOTE])
        result = provider.prices(["TESTX"])
        self.assertEqual("available", result["status"])
        self.assertEqual("2.3100", result["quotes"]["TESTX"]["price"])
        self.assertEqual(QUOTE["as_of"], result["quotes"]["TESTX"]["as_of"])
        provenance = result["sources"][0]
        self.assertEqual(QUOTE["as_of"], provenance["source_timestamp"])
        self.assertEqual("2026-10-08T14:00:00Z", provenance["retrieved_at"])
        self.assertIsNone(provenance["delay_seconds"])
        self.assertFalse(provenance["freshness_verified"])
        self.assertFalse(provenance["executable_nbbo"])
        self.assertEqual("IEX_ONLY_NOT_CONSOLIDATED", provenance["coverage"])
        self.assertEqual(PRICE_URL, provider.transport.calls[0][0])
        self.assertEqual({"User-Agent", "Accept", "Accept-Encoding"}, set(provider.transport.calls[0][1]))

    def test_null_evidence_is_partial_never_substituted(self):
        quote = {**QUOTE, "as_of": None, "volume": None, "previous_close": None, "trading_day": None}
        result = source([quote]).prices(["TESTX"])
        self.assertEqual("partial", result["status"])
        for key in ("as_of", "volume", "previous_close", "trading_day"):
            self.assertIsNone(result["quotes"]["TESTX"][key])
        self.assertIn("fintable_timestamp_missing", result["errors"])
        self.assertIsNone(result["sources"][0]["source_timestamp"])

    def test_missing_requested_symbols_are_reported_without_synthetic_rows(self):
        result = source([QUOTE]).prices(["TESTX", "TESTY"])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["TESTY"], result["missing_symbols"])
        self.assertEqual({"TESTX"}, set(result["quotes"]))
        self.assertEqual(["fintable_symbol_missing"], result["errors"])
        self.assertEqual("unavailable", source([]).prices(["TESTX"])["status"])

    def test_batch_has_no_single_invented_as_of(self):
        result = source([QUOTE, {**QUOTE, "symbol": "TESTY", "as_of": "2026-10-08T13:45:00Z"}]).prices(["TESTX", "TESTY"])
        self.assertIsNone(result["sources"][0]["source_timestamp"])
        self.assertEqual("2026-10-08T13:45:00Z", result["quotes"]["TESTY"]["as_of"])

    def test_duplicate_unrequested_or_malformed_rows_fail_closed(self):
        cases = [([QUOTE, QUOTE], "fintable_symbol_duplicate"),
                 ([{**QUOTE, "symbol": "TESTY"}], "fintable_symbol_unrequested")]
        for key, bad in (("price", "NaN"), ("price", 2.0), ("price", "0"),
                         ("volume", True), ("volume", -1), ("currency", "TRY"), ("feed", "sip"),
                         ("previous_close", "-2"), ("trading_day", "2026-02-30"),
                         ("as_of", "2026-10-08T13:59:00"), ("as_of", "invalid")):
            cases.append(([{**QUOTE, key: bad}], None))
        cases.append(([{key: value for key, value in QUOTE.items() if key != "as_of"}], None))
        for rows, error in cases:
            with self.subTest(rows=rows):
                result = source(rows).prices(["TESTX"])
                self.assertEqual("unavailable", result["status"])
                self.assertEqual({}, result["quotes"])
                if error:
                    self.assertEqual([error], result["errors"])

    def test_invalid_symbols_are_rejected_before_network(self):
        provider = source([QUOTE])
        for symbols in ([], ["TESTX"] * 2, ["testx"], ["TESTX&token=secret"], "TESTX",
                        ["TEST" + str(i) for i in range(21)], [True], [{"bad": "shape"}]):
            self.assertEqual("unavailable", provider.prices(symbols)["status"])
        self.assertEqual([], provider.transport.calls)

    def test_bad_json_encoding_and_exception_text_are_sanitized(self):
        for data in (b'{"data": [], "data": []}', b'{"data": NaN}', b'\xff', b'{',
                     RuntimeError("synthetic secret"), PublicSourceError("synthetic secret")):
            result = source(data).prices(["TESTX"])
            self.assertEqual("unavailable", result["status"])
            self.assertNotIn("synthetic secret", json.dumps(result))

    def test_retrieval_timestamp_is_recorded_after_response(self):
        provider = source([QUOTE])
        with patch.object(provider, "_now", side_effect=[NOW, NOW + timedelta(seconds=2)]):
            result = provider.prices(["TESTX"])
        self.assertEqual("2026-10-08T14:00:00Z", result["sources"][0]["attempted_at"])
        self.assertEqual("2026-10-08T14:00:02Z", result["sources"][0]["retrieved_at"])


class FintableHistoryTests(unittest.TestCase):
    def test_history_sorts_original_timestamps_and_locally_filters_requested_day(self):
        later = {**BAR, "timestamp": "2026-10-08T13:55:00Z"}
        next_day = {**BAR, "timestamp": "2026-10-09T13:50:00Z", "date": "2026-10-09"}
        provider = source({**HISTORY, "bars": [later, next_day, BAR]})
        result = provider.history("TESTX", "2026-10-08")
        self.assertEqual("available", result["status"])
        self.assertEqual([BAR, later], result["data"]["bars"])
        self.assertEqual(HISTORY_URL, provider.transport.calls[0][0])
        self.assertEqual(later["timestamp"], result["sources"][0]["source_timestamp"])
        self.assertEqual("split_and_dividend_adjusted", result["sources"][0]["historical_adjustment"])

    def test_empty_duplicate_and_truncated_history_are_unavailable(self):
        for bars, error in (([], "fintable_history_empty"), ([BAR, BAR], "fintable_bar_duplicate"),
                            ([BAR] * 1000, "fintable_history_truncated")):
            result = source({**HISTORY, "bars": bars}).history("TESTX", "2026-10-08")
            self.assertEqual("unavailable", result["status"])
            self.assertIsNone(result["data"])
            self.assertEqual([error], result["errors"])

    def test_bad_bar_ohlc_dates_timestamps_types_and_feed_are_rejected(self):
        cases = [{**HISTORY, "symbol": "TESTY"}, {**HISTORY, "feed": "sip"}, {**HISTORY, "timeframe": "1day"}]
        for key, bad in (("volume", True), ("trade_count", -1), ("high", "1"),
                         ("vwap", "3"), ("close", "9"), ("timestamp", None),
                         ("timestamp", "2026-10-07T13:50:00Z"), ("date", "2026-10-07")):
            cases.append({**HISTORY, "bars": [{**BAR, key: bad}]})
        for payload in cases:
            with self.subTest(payload=payload):
                result = source(payload).history("TESTX", "2026-10-08")
                self.assertEqual("unavailable", result["status"])
                self.assertIsNone(result["data"])

    def test_invalid_symbol_or_day_has_no_network_request(self):
        provider = source(HISTORY)
        for symbol, day in (("testx", "2026-10-08"), ("TESTX", "2026-02-30"), ("TESTX", None)):
            self.assertEqual("unavailable", provider.history(symbol, day)["status"])
        self.assertEqual([], provider.transport.calls)


if __name__ == "__main__":
    unittest.main()
