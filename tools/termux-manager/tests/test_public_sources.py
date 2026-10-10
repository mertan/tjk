"""Synthetic metadata/transport fixtures; no live quotes or network calls."""

import copy
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from equity_guard.public_sources import (
    NASDAQ_DIRECTORY, NYSE_DIRECTORY, SEC_MAPPING, PublicSourceError,
    PublicSources, PublicTransport, _NoRedirect, allowed_url,
    parse_directory, parse_sec_mapping,
)

NOW = datetime(2026, 10, 8, 14, tzinfo=timezone.utc)
NASDAQ_HEADER = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
NYSE_HEADER = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol"
FOOTER = "File Creation Time: 1008202610:00|||||||"
NASDAQ_TEXT = "\n".join((NASDAQ_HEADER, "TESTX|Synthetic Common Stock|Q|N|N|100|N|N", FOOTER))
NYSE_TEXT = "\n".join((NYSE_HEADER, "TESTY|Synthetic Common Stock|N|TESTY|N|100|N|TESTY", FOOTER))
SEC_URL = "https://data.sec.gov/submissions/CIK0000001234.json"
SEC_PAYLOAD = {"tickers": ["TESTX"], "filings": {"recent": {
    "filingDate": ["2026-10-07", "2025-09-01"], "form": ["10-Q", "10-K"],
    "acceptanceDateTime": ["2026-10-07T12:00:00Z", "2025-09-01T12:00:00Z"],
    "items": ["", ""]}, "files": []}}
MAPPING = {"fields": ["cik", "name", "ticker", "exchange"],
           "data": [[1234, "Synthetic", "TESTX", "Nasdaq"], [4321, "Synthetic", "TESTY", "NYSE"]]}


class Response(io.BytesIO):
    def __init__(self, body=b"example", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class FixtureTransport:
    def __init__(self, payloads=None):
        self.payloads = payloads or {
            NASDAQ_DIRECTORY: NASDAQ_TEXT.encode(), NYSE_DIRECTORY: NYSE_TEXT.encode(),
            SEC_MAPPING: json.dumps(MAPPING).encode(), SEC_URL: json.dumps(SEC_PAYLOAD).encode()}
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, headers))
        value = self.payloads[url]
        if isinstance(value, Exception):
            raise value
        return value


class PublicTransportTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.opener.open.return_value = Response()
        self.transport = PublicTransport(opener=self.opener)

    def test_allowlist_is_exact_and_quotes_or_orders_are_never_allowed(self):
        for url in (NASDAQ_DIRECTORY, NYSE_DIRECTORY, SEC_MAPPING, SEC_URL,
                    "https://data.sec.gov/submissions/CIK0000001234-submissions-001.json"):
            self.assertTrue(allowed_url(url), url)
        for url in ("http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                    "https://nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                    NASDAQ_DIRECTORY + "?x=1", NASDAQ_DIRECTORY + "#x", NASDAQ_DIRECTORY + "/",
                    "https://www.sec.gov/files/company_tickers.json",
                    "https://secret@data.sec.gov/submissions/CIK0000001234.json",
                    "https://data.sec.gov:444/submissions/CIK0000001234.json",
                    "https://data.sec.gov/submissions/../CIK0000001234.json",
                    "https://data.sec.gov.evil.test/submissions/CIK0000001234.json",
                    "https://paper-api.alpaca.markets/v2/orders",
                    "https://data.alpaca.markets/v2/stocks/snapshots",
                    "https://finviz.com/screener.ashx", "https://scanner.tradingview.com/america/scan"):
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url))
                with self.assertRaisesRegex(PublicSourceError, "^endpoint_rejected$"):
                    self.transport.get(url)
        self.opener.open.assert_not_called()

    def test_readonly_get_uses_bounded_timeout_and_no_auth_header(self):
        self.assertEqual(b"example", self.transport.get(NASDAQ_DIRECTORY, {"Accept-Encoding": "identity"}))
        request = self.opener.open.call_args.args[0]
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.data)
        self.assertLessEqual(self.opener.open.call_args.kwargs["timeout"], 12)
        self.assertIsNone(request.get_header("Authorization"))

    def test_default_opener_preserves_proxy_handler_and_tls_verification(self):
        with patch("equity_guard.public_sources.build_opener", return_value=self.opener) as build:
            with patch("equity_guard.public_sources.ssl.create_default_context") as context:
                PublicTransport()
        context.assert_called_once_with()
        # No replacement ProxyHandler is provided, so build_opener adds the
        # standard proxy handler using the runtime's HTTP(S)_PROXY settings.
        self.assertEqual(["_NoRedirect", "HTTPSHandler"], [type(h).__name__ for h in build.call_args.args])

    def test_redirect_is_never_followed(self):
        with self.assertRaisesRegex(PublicSourceError, "^redirect_rejected$"):
            _NoRedirect().redirect_request(None, None, 302, "ignored", {}, "https://evil.invalid")

    def test_credentials_cannot_be_forwarded(self):
        for headers in ({"Authorization": "synthetic"}, {"APCA-API-KEY-ID": "synthetic"},
                        {"Cookie": "synthetic"}, {"User-Agent": "line\nbreak"}):
            with self.subTest(headers=list(headers)):
                with self.assertRaises(PublicSourceError):
                    self.transport.get(NASDAQ_DIRECTORY, headers)
        self.opener.open.assert_not_called()

    def test_sec_requires_identifying_user_agent(self):
        with self.assertRaisesRegex(PublicSourceError, "^sec_user_agent_required$"):
            self.transport.get(SEC_MAPPING)
        self.opener.open.assert_not_called()

    def test_sec_uses_existing_processwide_two_requests_per_second_pacer(self):
        with patch("equity_guard.public_sources._pace_sec") as pace:
            self.transport.get(SEC_MAPPING, {"User-Agent": "Synthetic test@example.invalid"})
        pace.assert_called_once_with(self.transport.deadline)

    def test_403_or_429_stops_collection_without_retry(self):
        for status in (403, 429):
            opener = Mock()
            opener.open.side_effect = HTTPError(NASDAQ_DIRECTORY, status, "sensitive body", {}, io.BytesIO())
            transport = PublicTransport(opener=opener)
            with self.assertRaisesRegex(PublicSourceError, "^http_" + str(status) + "$"):
                transport.get(NASDAQ_DIRECTORY)
            with self.assertRaisesRegex(PublicSourceError, "^source_access_stopped$"):
                transport.get(NYSE_DIRECTORY)
            self.assertEqual(1, opener.open.call_count)

    def test_server_errors_have_no_retry_or_response_detail(self):
        self.opener.open.side_effect = HTTPError(NASDAQ_DIRECTORY, 500, "sensitive", {}, io.BytesIO())
        with self.assertRaisesRegex(PublicSourceError, "^http_500$"):
            self.transport.get(NASDAQ_DIRECTORY)
        self.assertEqual(1, self.opener.open.call_count)

    def test_network_exception_is_sanitized(self):
        self.opener.open.side_effect = URLError("synthetic secret detail")
        with self.assertRaisesRegex(PublicSourceError, "^network_unavailable$"):
            self.transport.get(NASDAQ_DIRECTORY)

    def test_request_budget(self):
        transport = PublicTransport(opener=self.opener, request_budget=1)
        transport.get(NASDAQ_DIRECTORY)
        with self.assertRaisesRegex(PublicSourceError, "^request_budget_exceeded$"):
            transport.get(NYSE_DIRECTORY)
        self.assertEqual(1, self.opener.open.call_count)

    def test_elapsed_budget(self):
        self.transport.deadline = -1
        with self.assertRaisesRegex(PublicSourceError, "^collection_budget_exceeded$"):
            self.transport.get(NASDAQ_DIRECTORY)
        self.opener.open.assert_not_called()

    def test_body_cap_and_compression_rejection(self):
        with patch("equity_guard.providers.MAX_BODY", 4):
            with self.assertRaisesRegex(PublicSourceError, "^response_too_large$"):
                self.transport.get(NASDAQ_DIRECTORY)
        self.opener.open.return_value = Response(headers={"Content-Encoding": "gzip"})
        with self.assertRaisesRegex(PublicSourceError, "^content_encoding_rejected$"):
            self.transport.get(NASDAQ_DIRECTORY)

    def test_invalid_budgets_cannot_disable_bounds(self):
        for kwargs in ({"request_budget": 25}, {"request_budget": True}, {"seconds_budget": 61},
                       {"seconds_budget": float("nan")}, {"seconds_budget": 0}):
            with self.assertRaisesRegex(PublicSourceError, "^budget_invalid$"):
                PublicTransport(**kwargs)


class DirectoryTests(unittest.TestCase):
    def test_metadata_preserves_unknown_timezone_and_never_claims_quote_time(self):
        result = parse_directory(NASDAQ_TEXT, NASDAQ_DIRECTORY, NOW)
        self.assertEqual("NASDAQ", result["entries"][0]["exchange"])
        self.assertEqual("unverified", result["entries"][0]["kind"])
        self.assertEqual("1008202610:00", result["source"]["source_timestamp_raw"])
        self.assertIsNone(result["source"]["source_timestamp"])
        self.assertIsNone(result["source"]["delay_seconds"])
        self.assertFalse(result["source"]["freshness_verified"])
        self.assertEqual("2026-10-08T14:00:00Z", result["source"]["retrieved_at"])

    def test_nasdaq_excludes_test_etf_nextshares_financial_flags_and_obvious_nonstocks(self):
        rows = ["TESTX|Synthetic Common Stock|Q|N|N|100|N|N",
                "TESTA|Synthetic Common Stock|Q|Y|N|100|N|N",
                "TESTB|Synthetic Common Stock|Q|N|N|100|Y|N",
                "TESTC|Synthetic Common Stock|Q|N|N|100|N|Y",
                "TESTD|Synthetic Common Stock|Q|N|D|100|N|N",
                "TESTE|Synthetic Warrants|Q|N|N|100|N|N",
                "TESTF|Synthetic Units|Q|N|N|100|N|N",
                "TESTG|Synthetic Preferred Stock|Q|N|N|100|N|N"]
        result = parse_directory("\n".join([NASDAQ_HEADER, *rows, FOOTER]), NASDAQ_DIRECTORY, NOW)
        self.assertEqual(["TESTX"], [x["symbol"] for x in result["entries"]])

    def test_otherlisted_only_accepts_nyse_not_amex_arca_or_bats(self):
        rows = ["TESTY|Synthetic Common Stock|N|TESTY|N|100|N|TESTY"]
        for exchange in ("A", "P", "Z", "V"):
            symbol = "TEST" + exchange
            rows.append(f"{symbol}|Synthetic Common Stock|{exchange}|{symbol}|N|100|N|{symbol}")
        result = parse_directory("\n".join([NYSE_HEADER, *rows, FOOTER]), NYSE_DIRECTORY, NOW)
        self.assertEqual(["TESTY"], [x["symbol"] for x in result["entries"]])
        self.assertEqual("NYSE", result["entries"][0]["exchange"])

    def test_missing_malformed_stale_future_footers_rejected(self):
        for footer, error in (("", "directory_timestamp_missing"), ("File Creation Time: bad", "directory_timestamp_missing"),
                              ("File Creation Time: 0230202610:00", "directory_timestamp_invalid"),
                              ("File Creation Time: 0920202610:00", "directory_stale"),
                              ("File Creation Time: 1010202610:00", "directory_timestamp_future")):
            with self.subTest(footer=footer):
                text = "\n".join((NASDAQ_HEADER, "TESTX|Synthetic Common Stock|Q|N|N|100|N|N", footer))
                with self.assertRaises(PublicSourceError) as exc:
                    parse_directory(text, NASDAQ_DIRECTORY, NOW)
                self.assertIn(exc.exception.code, {error, "directory_schema_invalid"})

    def test_bad_schema_duplicates_or_flags_rejected(self):
        for text in (NASDAQ_TEXT.replace("Test Issue", "Missing"),
                     NASDAQ_TEXT.replace("|N|N|100", "|?|N|100"),
                     NASDAQ_TEXT.replace(FOOTER, "TESTX|Duplicate Common Stock|Q|N|N|100|N|N\n" + FOOTER),
                     NASDAQ_TEXT.replace("|Q|N|N|100", "|Q|N|N")):
            with self.assertRaises(PublicSourceError):
                parse_directory(text, NASDAQ_DIRECTORY, NOW)


class PublicSourcesTests(unittest.TestCase):
    def setUp(self):
        self.transport = FixtureTransport()
        self.sources = PublicSources({"SEC_USER_AGENT": "Synthetic test@example.invalid"}, self.transport, NOW)

    def test_universe_fetches_both_exchanges_without_prices(self):
        result = self.sources.universe()
        self.assertEqual("available", result["status"])
        self.assertEqual({"NASDAQ", "NYSE"}, {x["exchange"] for x in result["entries"]})
        self.assertTrue(all("price" not in x and "quote" not in x for x in result["entries"]))
        self.assertEqual([NASDAQ_DIRECTORY, NYSE_DIRECTORY], [x[0] for x in self.transport.calls])

    def test_partial_directory_failure_cannot_claim_full_universe(self):
        self.transport.payloads[NYSE_DIRECTORY] = PublicSourceError("http_403")
        result = self.sources.universe()
        self.assertEqual("unavailable", result["status"])
        self.assertEqual([], result["entries"])
        self.assertEqual(["http_403"], result["errors"])
        self.assertEqual("available", result["sources"][0]["status"])
        failed = result["sources"][1]
        self.assertEqual(NYSE_DIRECTORY, failed["url"])
        self.assertEqual("unavailable", failed["status"])
        self.assertEqual("2026-10-08T14:00:00Z", failed["attempted_at"])
        self.assertEqual("http_403", failed["error"])
        self.assertIsNone(failed["retrieved_at"])
        self.assertIsNone(failed["source_timestamp"])
        self.assertIsNone(failed["delay_seconds"])

    def test_complete_network_failure_still_reports_both_source_attempts(self):
        self.transport.payloads[NASDAQ_DIRECTORY] = PublicSourceError("network_unavailable")
        self.transport.payloads[NYSE_DIRECTORY] = PublicSourceError("network_unavailable")
        result = self.sources.universe()
        self.assertEqual("unavailable", result["status"])
        self.assertEqual([NASDAQ_DIRECTORY, NYSE_DIRECTORY], [s["url"] for s in result["sources"]])
        self.assertTrue(all(s["retrieved_at"] is None for s in result["sources"]))
        self.assertEqual(result["sources"], self.sources.sources)

    def test_live_collection_timestamp_is_after_each_retrieval_not_scan_start(self):
        sources = PublicSources({}, self.transport)
        instants = [NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3), NOW + timedelta(seconds=5)]
        with patch.object(sources, "_now", side_effect=instants):
            result = sources.universe()
        self.assertEqual("2026-10-08T14:00:00Z", result["sources"][0]["attempted_at"])
        self.assertEqual("2026-10-08T14:00:02Z", result["sources"][0]["retrieved_at"])
        self.assertEqual("2026-10-08T14:00:03Z", result["sources"][1]["attempted_at"])
        self.assertEqual("2026-10-08T14:00:05Z", result["sources"][1]["retrieved_at"])

    def test_cross_exchange_duplicate_fails_closed(self):
        self.transport.payloads[NYSE_DIRECTORY] = NYSE_TEXT.replace("TESTY", "TESTX").encode()
        result = self.sources.universe()
        self.assertEqual([], result["entries"])
        self.assertIn("directory_cross_exchange_duplicate", result["errors"])

    def test_reads_only_sec_user_agent_from_environment(self):
        class GuardedEnv:
            def get(self, key, default=None):
                if key != "SEC_USER_AGENT":
                    raise AssertionError("Other environment variable read")
                return "Synthetic test@example.invalid"
        sources = PublicSources(GuardedEnv(), self.transport, NOW)
        self.assertEqual("available", sources.symbol_map()["status"])

    def test_mapping_has_no_claim_of_source_time_or_verified_completeness(self):
        result = self.sources.symbol_map()
        self.assertEqual({"cik": "0000001234", "exchange": "NASDAQ"}, result["mapping"]["TESTX"])
        self.assertFalse(result["sources"][0]["freshness_verified"])
        self.assertIsNone(result["sources"][0]["source_timestamp"])

    def test_mapping_missing_agent_does_not_call_network(self):
        result = PublicSources({}, self.transport, NOW).symbol_map()
        self.assertEqual(["sec_user_agent_required"], result["errors"])
        self.assertEqual([], self.transport.calls)

    def test_mapping_ambiguity_is_not_resolved_by_guessing(self):
        payload = copy.deepcopy(MAPPING)
        payload["data"].append([9999, "Synthetic", "TESTX", "Nasdaq"])
        self.assertNotIn("TESTX", parse_sec_mapping(payload))

    def test_mapping_malformed_ciks_and_schema_rejected(self):
        for cik in (True, 0, -1, "1.5", "12345678901"):
            payload = copy.deepcopy(MAPPING)
            payload["data"][0][0] = cik
            with self.assertRaisesRegex(PublicSourceError, "^sec_mapping_schema_invalid$"):
                parse_sec_mapping(payload)
        with self.assertRaises(PublicSourceError):
            parse_sec_mapping({"fields": ["cik", "cik"], "data": []})

    def test_injected_error_never_exposes_exception_detail(self):
        self.transport.payloads[SEC_MAPPING] = RuntimeError("synthetic sensitive text")
        result = self.sources.symbol_map()
        self.assertEqual(["source_request_failed"], result["errors"])
        self.assertEqual(SEC_MAPPING, result["sources"][0]["url"])
        self.assertEqual("source_request_failed", result["sources"][0]["error"])
        self.assertIsNone(result["sources"][0]["retrieved_at"])
        self.assertNotIn("sensitive", json.dumps(result))

    def test_sec_filings_reuses_risk_rules_and_cannot_clear_without_review(self):
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("unknown", result["status"])
        self.assertEqual(365, result["coverage_days"])
        self.assertEqual([], result["flags"])

    def test_registration_and_financing_metadata_remain_risk(self):
        for form, items in (("S-3ASR", ""), ("424B5", ""), ("8-K", "3.02")):
            payload = copy.deepcopy(SEC_PAYLOAD)
            payload["filings"]["recent"]["form"][0] = form
            payload["filings"]["recent"]["items"][0] = items
            self.transport.payloads[SEC_URL] = json.dumps(payload).encode()
            result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
            self.assertEqual("risk", result["status"])
            self.assertTrue(result["flags"])

    def test_filings_review_requires_current_complete_metadata(self):
        review = {"status": "clear", "checked_at": "2026-10-07T14:00:00Z", "coverage_days": 365,
                  "latest_filing_date": "2026-10-07", "source_url": "https://www.sec.gov/Archives/test",
                  "reviewed_by": "synthetic-reviewer"}
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234", "filings_review": review})
        self.assertEqual("clear", result["status"])
        payload = copy.deepcopy(SEC_PAYLOAD)
        payload["filings"]["recent"]["filingDate"][-1] = "2026-01-01"
        self.transport.payloads[SEC_URL] = json.dumps(payload).encode()
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234", "filings_review": review})
        self.assertEqual("unknown", result["status"])

    def test_filings_preserves_safe_access_error(self):
        self.transport.payloads[SEC_URL] = PublicSourceError("http_429")
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("unknown", result["status"])
        self.assertEqual("http_429", result["error"])
        self.assertEqual(SEC_URL, result["sources"][0]["url"])
        self.assertEqual("http_429", result["sources"][0]["error"])
        self.assertIsNone(result["sources"][0]["retrieved_at"])

    def test_filings_sources_are_scoped_to_this_call(self):
        self.sources.symbol_map()
        first = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        second = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        for result in (first, second):
            self.assertEqual(1, len(result["sources"]))
            self.assertEqual(SEC_URL, result["sources"][0]["url"])
            self.assertEqual("available", result["sources"][0]["status"])
            self.assertEqual("2026-10-08T14:00:00Z", result["sources"][0]["retrieved_at"])
        self.assertEqual(3, len(self.sources.sources))

    def test_filings_sources_include_success_then_archive_failure(self):
        archive_url = "https://data.sec.gov/submissions/CIK0000001234-submissions-001.json"
        payload = copy.deepcopy(SEC_PAYLOAD)
        payload["filings"]["files"] = [{"name": "CIK0000001234-submissions-001.json", "filingTo": "2026-01-01"}]
        self.transport.payloads[SEC_URL] = json.dumps(payload).encode()
        self.transport.payloads[archive_url] = PublicSourceError("http_403")
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("unknown", result["status"])
        self.assertEqual([SEC_URL, archive_url], [s["url"] for s in result["sources"]])
        self.assertEqual(["available", "unavailable"], [s["status"] for s in result["sources"]])

    def test_malformed_filings_json_reports_failed_source_without_body(self):
        self.transport.payloads[SEC_URL] = b"<html>sensitive response details</html>"
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("sec_schema_invalid", result["error"])
        self.assertEqual("sec_schema_invalid", result["sources"][0]["error"])
        self.assertNotIn("sensitive", json.dumps(result))

    def test_filings_missing_user_agent_and_cik_fail_without_network(self):
        result = PublicSources({}, self.transport, NOW).filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("sec_user_agent_required", result["error"])
        self.assertEqual("sec_cik_required", self.sources.filings({"symbol": "TESTX"})["error"])
        self.assertEqual([], self.transport.calls)

    def test_symbol_cik_mismatch_is_not_a_clear_review(self):
        self.transport.payloads[SEC_URL] = json.dumps({**SEC_PAYLOAD, "tickers": ["OTHER"]}).encode()
        result = self.sources.filings({"symbol": "TESTX", "cik": "1234"})
        self.assertEqual("unknown", result["status"])
        self.assertEqual("sec_symbol_cik_mismatch", result["error"])


if __name__ == "__main__":
    unittest.main()
