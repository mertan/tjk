"""Synthetic transport fixtures only: none of these prices are market data."""

import copy
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from equity_guard.providers import (
    DATA, PAPER, SEC, MAX_PAGES, NY, ProviderError, ReadOnlyProvider,
    _allowed_url, _iso, _metrics, get_json,
)


NOW = datetime(2026, 10, 8, 14, 1, tzinfo=timezone.utc)
ENV = {"ALPACA_API_KEY_ID": "SYNTHETIC_KEY", "ALPACA_API_SECRET_KEY": "SYNTHETIC_SECRET",
       "ALPACA_SIP_CONFIRMED": "yes", "SEC_USER_AGENT": "Synthetic test test@example.invalid"}


def fixtures():
    days = []
    current = NOW.astimezone(NY).date()
    while len(days) < 11:
        if current.weekday() < 5:
            days.append({"date": current.isoformat(), "open": "09:30", "close": "16:00"})
        current -= timedelta(days=1)
    days.reverse()
    bars = []
    for day in days:
        opened = datetime.fromisoformat(day["date"] + "T09:30:00").replace(tzinfo=NY)
        for i in range(6 if day == days[-1] else 78):
            bars.append({"t": _iso(opened + timedelta(minutes=i * 5)), "o": 2.0, "h": 2.1,
                         "l": 1.99, "c": 2.0 if i == 77 else 2.06,
                         "v": 3000 if day == days[-1] and i == 5 else 1000, "vw": 2.04})
    context = {"symbols": [{"symbol": "TESTX", "cik": "1234", "kind": "stock", "exchange": "NASDAQ",
                            "broker_available": True, "broker_verified_at": _iso(NOW),
                            "news_review": {"status": "verified", "id": "99", "url": "https://www.benzinga.com/test",
                                            "published_at": "2026-10-08T13:00:00Z", "reviewed_at": "2026-10-08T13:30:00Z",
                                            "category": "earnings", "material": True},
                            "filings_review": {"status": "clear", "checked_at": "2026-10-07T14:00:00Z",
                                               "source_url": "https://www.sec.gov/Archives/test-review",
                                               "latest_filing_date": "2026-10-07", "coverage_days": 365,
                                               "reviewed_by": "synthetic-reviewer"}}],
               "fx": {"synthetic": True}, "account": {}, "costs": {}}
    payloads = {
        PAPER + "/v2/clock": {"timestamp": _iso(NOW), "is_open": True},
        PAPER + "/v2/calendar": days,
        PAPER + "/v2/assets": [{"symbol": "TESTX", "exchange": "NASDAQ", "status": "active", "class": "us_equity"}],
        DATA + "/v2/stocks/snapshots": {"TESTX": {
            "latestQuote": {"t": _iso(NOW - timedelta(seconds=1)), "bp": 2.05, "ap": 2.06, "bs": 100, "as": 200, "c": ["R"]},
            "latestTrade": {"t": _iso(NOW - timedelta(seconds=2)), "p": 2.06},
            "prevDailyBar": {"t": "2026-10-07T04:00:00Z", "c": 2.0},
            "dailyBar": {"t": "2026-10-08T04:00:00Z", "c": 2.06}}},
        DATA + "/v2/stocks/bars": {"bars": {"TESTX": bars}, "next_page_token": None},
        DATA + "/v1beta1/news": {"news": [{"id": 99, "source": "benzinga", "url": "https://www.benzinga.com/test",
                                          "symbols": ["TESTX"], "created_at": "2026-10-08T13:00:00Z",
                                          "updated_at": "2026-10-08T13:00:00Z", "headline": "SYNTHETIC earnings fixture"}]},
        SEC + "/submissions/CIK0000001234.json": {"tickers": ["TESTX"], "filings": {"recent": {
            "filingDate": ["2026-10-07", "2025-09-01"], "form": ["10-Q", "10-K"],
            "acceptanceDateTime": ["2026-10-07T12:00:00Z", "2025-09-01T12:00:00Z"], "items": ["", ""]}, "files": []}},
    }
    calls = []

    def getter(url, headers, params):
        calls.append((url, copy.deepcopy(headers), copy.deepcopy(params)))
        return copy.deepcopy(payloads[url])

    return context, payloads, calls, getter


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.context, self.payloads, self.calls, self.getter = fixtures()

    def collect(self, env=None):
        return ReadOnlyProvider(self.getter, ENV if env is None else env).collect(self.context, NOW)

    def test_no_credentials_means_no_network_and_no_quotes(self):
        result = self.collect({})
        self.assertIn("alpaca_credentials_missing", result["errors"])
        self.assertEqual([], self.calls)
        self.assertEqual([], result["securities"])
        self.assertFalse(result["quote_source"]["realtime"])

    def test_iex_cannot_replace_unconfirmed_sip(self):
        result = self.collect({**ENV, "ALPACA_SIP_CONFIRMED": "no"})
        self.assertIn("real_time_sip_entitlement_unconfirmed", result["errors"])
        self.assertEqual([], self.calls)

    def test_synthetic_valid_pipeline_exact_sizes_and_matched_rvol(self):
        result = self.collect()
        self.assertEqual([], result["errors"])
        security = result["securities"][0]
        self.assertEqual(200, security["quote"]["ask_size"])
        self.assertEqual(["R"], security["quote"]["conditions"])
        self.assertEqual(3, security["metrics"]["rvol_5m"])
        self.assertEqual(8000, security["metrics"]["day_volume"])
        self.assertEqual("2026-10-08T14:00:00Z", security["metrics"]["window_end"])
        self.assertEqual(_iso(NOW), security["metrics"]["as_of"])
        self.assertEqual("verified", security["news"]["status"])
        self.assertEqual("clear", security["filings"]["status"])
        for url, headers, params in self.calls:
            self.assertTrue(_allowed_url(url))
            self.assertNotIn("orders", url)
            if url.startswith(SEC):
                self.assertNotIn("APCA-API-KEY-ID", headers)
            if "/stocks/" in url:
                self.assertEqual("sip", params["feed"])

    def test_market_closed_avoids_security_data(self):
        self.payloads[PAPER + "/v2/clock"]["is_open"] = False
        result = self.collect()
        self.assertIn("regular_market_closed", result["errors"])
        self.assertEqual(2, len(self.calls))

    def test_stale_quote_fails_without_fallback(self):
        self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestQuote"]["t"] = _iso(NOW - timedelta(seconds=16))
        result = self.collect()
        self.assertIn("TESTX:quote_or_trade_stale", result["errors"])
        self.assertNotIn("metrics", result["securities"][0])

    def test_nonfirm_closed_unknown_or_mixed_quote_conditions_fail_closed(self):
        for conditions in (["N"], ["U"], ["L"], ["Z"], ["UNKNOWN"], ["R", "N"],
                           ["R", "R"], [], "R", None, {"R": True}):
            with self.subTest(conditions=conditions):
                self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestQuote"]["c"] = conditions
                result = self.collect()
                self.assertIn("TESTX:regular_quote_condition_unverified", result["errors"])
                self.assertNotIn("quote", result["securities"][0])
                self.assertNotIn("metrics", result["securities"][0])

    def test_missing_quote_conditions_fail_closed(self):
        self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestQuote"].pop("c")
        self.assertIn("TESTX:regular_quote_condition_unverified", self.collect()["errors"])

    def test_missing_historical_bar_fails(self):
        self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"].pop(0)
        self.assertIn("TESTX:historical_or_current_bar_gap", self.collect()["errors"])

    def test_missing_today_bar_fails(self):
        self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"].pop()
        self.assertIn("TESTX:historical_or_current_bar_gap", self.collect()["errors"])

    def test_duplicate_bar_fails(self):
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        bars.append(bars[-1].copy())
        self.assertIn("TESTX:duplicate_bar", self.collect()["errors"])

    def test_bad_universe_does_not_network(self):
        for symbols in ([], [{"symbol": "../../orders"}], self.context["symbols"] * 21):
            self.context["symbols"] = symbols
            result = self.collect()
            self.assertTrue(result["errors"])
        self.assertEqual([], self.calls)

    def test_unreviewed_news_never_inferred_from_headline(self):
        self.context["symbols"][0].pop("news_review")
        result = self.collect()
        self.assertEqual("unknown", result["securities"][0]["news"]["status"])
        candidates = result["securities"][0]["news"]["review_candidates"]
        self.assertEqual(["99"], [item["id"] for item in candidates])
        self.assertNotIn("material", candidates[0])
        self.assertTrue(any(url.endswith("/news") for url, _, _ in self.calls))

    def test_reviewed_news_must_match_provider(self):
        self.context["symbols"][0]["news_review"]["url"] = "https://unverified.invalid"
        self.assertIn("TESTX:news_review_source_mismatch", self.collect()["errors"])

    def test_news_update_after_manual_review_invalidates_review(self):
        self.payloads[DATA + "/v1beta1/news"]["news"][0]["updated_at"] = "2026-10-08T13:45:00Z"
        self.assertIn("TESTX:news_review_time_mismatch", self.collect()["errors"])

    def test_sec_metadata_without_review_never_clears(self):
        self.context["symbols"][0].pop("filings_review")
        self.assertEqual("unknown", self.collect()["securities"][0]["filings"]["status"])

    def test_registration_flag_overrides_manual_clear(self):
        self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]["form"][0] = "424B5"
        result = self.collect()["securities"][0]["filings"]
        self.assertEqual("risk", result["status"])
        self.assertIn("REGISTRATION_OR_OFFERING:424B5", result["flags"])

    def test_automatic_shelf_and_additional_registration_forms_block_clear(self):
        for form in ("S-3ASR", "F-3ASR", "S-1MEF", "S-3MEF", "F-1MEF", "F-3MEF",
                     "S-3D", "S-3DPOS", "F-3D", "F-3DPOS", "POS AM", "POSASR"):
            with self.subTest(form=form):
                table = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
                table["form"][0] = form
                result = self.collect()["securities"][0]["filings"]
                self.assertEqual("risk", result["status"])
                self.assertIn("REGISTRATION_OR_OFFERING:" + form, result["flags"])

    def test_missing_filing_form_cannot_inherit_manual_clear(self):
        for form in ("", "   ", None, 0):
            with self.subTest(form=form):
                table = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
                table["form"][0] = form
                result = self.collect()
                self.assertIn("TESTX:sec_form_invalid", result["errors"])
                self.assertNotIn("filings", result["securities"][0])

    def test_financing_8k_item_is_risk(self):
        table = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
        table["form"][0], table["items"][0] = "8-K", "3.02"
        self.assertEqual("risk", self.collect()["securities"][0]["filings"]["status"])

    def test_missing_sec_identity_stays_unknown(self):
        self.assertEqual("unknown", self.collect({**ENV, "SEC_USER_AGENT": ""})["securities"][0]["filings"]["status"])

    def test_sec_archives_requested_and_coverage_completed(self):
        filings = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]
        filings["recent"] = {key: values[:1] for key, values in filings["recent"].items()}
        name = "CIK0000001234-submissions-001.json"
        filings["files"] = [{"name": name, "filingFrom": "2025-09-01", "filingTo": "2026-01-01"}]
        self.payloads[SEC + "/submissions/" + name] = {"filingDate": ["2026-01-01", "2025-09-01"], "form": ["10-Q", "10-K"]}
        self.assertEqual("clear", self.collect()["securities"][0]["filings"]["status"])
        self.assertTrue(any(url.endswith(name) for url, _, _ in self.calls))

    def test_sec_365_day_coverage_unproven_stays_unknown(self):
        filings = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]
        filings["recent"] = {key: values[:1] for key, values in filings["recent"].items()}
        result = self.collect()["securities"][0]["filings"]
        self.assertEqual("unknown", result["status"])
        self.assertIn("SEC_365_DAY_COVERAGE_UNPROVEN", result["flags"])

    def test_sec_archive_path_injection_rejected(self):
        self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["files"] = [
            {"name": "../../orders", "filingTo": "2026-10-01"}]
        self.assertIn("TESTX:sec_archive_path_invalid", self.collect()["errors"])

    def test_manual_filing_review_must_postdate_latest_filing(self):
        self.context["symbols"][0]["filings_review"]["checked_at"] = "2026-10-07T11:00:00Z"
        self.assertEqual("unknown", self.collect()["securities"][0]["filings"]["status"])

    def test_each_latest_day_filing_needs_acceptance_time(self):
        table = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
        for field, value in {"filingDate": "2026-10-07", "form": "10-Q", "acceptanceDateTime": "", "items": ""}.items():
            table[field].insert(0, value)
        self.assertEqual("unknown", self.collect()["securities"][0]["filings"]["status"])

    def test_unknown_acceptance_fallback_uses_new_york_day_end(self):
        table = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
        table["acceptanceDateTime"][0] = ""
        self.context["symbols"][0]["filings_review"]["checked_at"] = "2026-10-08T02:00:00Z"
        self.assertEqual("unknown", self.collect()["securities"][0]["filings"]["status"])

    def test_bars_pagination_is_complete(self):
        original = self.getter
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        def paged(url, headers, params):
            if url.endswith("/bars"):
                if not params.get("page_token"):
                    return {"bars": {"TESTX": bars[:30]}, "next_page_token": "page2"}
                return {"bars": {"TESTX": bars[30:]}, "next_page_token": None}
            return original(url, headers, params)
        self.getter = paged
        self.assertEqual([], self.collect()["errors"])

    def test_bars_pagination_repeat_fails_closed(self):
        self.payloads[DATA + "/v2/stocks/bars"]["next_page_token"] = "same"
        self.assertIn("TESTX:pagination_invalid", self.collect()["errors"])

    def test_provider_error_does_not_leak_secret(self):
        def broken(*args):
            raise RuntimeError("https://example.invalid/" + ENV["ALPACA_API_SECRET_KEY"])
        result = ReadOnlyProvider(broken, ENV).collect(self.context, NOW)
        self.assertEqual(["provider_request_failed"], result["errors"])
        self.assertNotIn(ENV["ALPACA_API_SECRET_KEY"], str(result))

    def test_snapshot_and_clock_are_last_network_requests(self):
        self.assertEqual([], self.collect()["errors"])
        self.assertEqual([DATA + "/v2/stocks/snapshots", PAPER + "/v2/clock"], [c[0] for c in self.calls[-2:]])
        self.assertEqual(2, sum(url.endswith("/clock") for url, _, _ in self.calls))

    def test_live_clock_advances_after_scan_started(self):
        original = self.getter
        current = [NOW]
        def changing(url, headers, params):
            if url.endswith("/stocks/bars"):
                current[0] = NOW + timedelta(seconds=10)
            if url.endswith("/stocks/snapshots"):
                for item in ("latestQuote", "latestTrade"):
                    self.payloads[url]["TESTX"][item]["t"] = _iso(current[0] - timedelta(seconds=1))
            if url.endswith("/clock"):
                self.payloads[url]["timestamp"] = _iso(current[0])
            return original(url, headers, params)
        class LiveClock(datetime):
            @classmethod
            def now(cls, tz=None): return current[0]
        with patch("equity_guard.providers.datetime", LiveClock):
            result = ReadOnlyProvider(changing, ENV).collect(self.context)
        self.assertEqual([], result["errors"])
        self.assertEqual(_iso(NOW + timedelta(seconds=9)), result["securities"][0]["quote"]["as_of"])
        self.assertEqual(_iso(NOW + timedelta(seconds=10)), result["market"]["as_of"])

    def test_partial_bar_cannot_become_complete_while_other_requests_run(self):
        original = self.getter
        started = NOW.replace(minute=4, second=55)
        finished = started + timedelta(seconds=10)
        current = [started]
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        # This bar is incomplete at the bar request, but its nominal bucket
        # ends before the scan's final clock. Without a request-time cutoff,
        # its unfinished volume/momentum masquerade as a completed window.
        bars.append({**bars[-1], "t": "2026-10-08T14:00:00Z"})

        def changing(url, headers, params):
            if url.endswith("/news"):
                current[0] = finished
            if url.endswith("/stocks/snapshots"):
                for item in ("latestQuote", "latestTrade"):
                    self.payloads[url]["TESTX"][item]["t"] = _iso(current[0] - timedelta(seconds=1))
            if url.endswith("/clock"):
                self.payloads[url]["timestamp"] = _iso(current[0])
            return original(url, headers, params)

        class LiveClock(datetime):
            @classmethod
            def now(cls, tz=None): return current[0]

        with patch("equity_guard.providers.datetime", LiveClock):
            result = ReadOnlyProvider(changing, ENV).collect(self.context)
        self.assertIn("TESTX:historical_or_current_bar_gap", result["errors"])
        self.assertNotIn("metrics", result["securities"][0])
        params = next(params for url, _, params in self.calls if url.endswith("/stocks/bars"))
        self.assertEqual(_iso(started), params["end"])

    def test_incomplete_current_bar_does_not_affect_completed_metrics(self):
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        bars.append({**bars[-1], "t": "2026-10-08T14:00:00Z", "v": 99999999})
        result = self.collect()
        self.assertEqual([], result["errors"])
        self.assertEqual(8000, result["securities"][0]["metrics"]["day_volume"])
        self.assertEqual(3, result["securities"][0]["metrics"]["rvol_5m"])

    def test_duplicate_prior_session_cannot_create_ten_day_rvol_baseline(self):
        calendar = self.payloads[PAPER + "/v2/calendar"]
        calendar[:] = [copy.deepcopy(calendar[-2]) for _ in range(10)] + [calendar[-1]]
        result = self.collect()
        self.assertIn("calendar_duplicate_session", result["errors"])
        self.assertEqual([], result["securities"])
        with self.assertRaisesRegex(ProviderError, "calendar_duplicate_session"):
            _metrics(self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"], calendar, NOW, 2.06, 2.0)

    def test_calendar_reversed_or_non_five_minute_session_fails(self):
        for opened, closed in (("16:00", "09:30"), ("09:31", "16:00"), ("09:30", "16:00:01")):
            with self.subTest(opened=opened, closed=closed):
                self.payloads[PAPER + "/v2/calendar"][-1].update(open=opened, close=closed)
                self.assertIn("calendar_invalid", self.collect()["errors"])

    def test_fresh_trade_drives_day_change(self):
        self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestTrade"]["p"] = 2.10
        result = self.collect()
        self.assertAlmostEqual(5.0, result["securities"][0]["metrics"]["day_change_pct"])

    def test_reverse_split_previous_close_is_ambiguous(self):
        self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["prevDailyBar"]["c"] = 0.2
        result = self.collect()
        self.assertIn("TESTX:previous_close_adjustment_ambiguous", result["errors"])
        self.assertNotIn("metrics", result["securities"][0])

    def test_previous_split_adjusted_close_required(self):
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        bars[:] = [bar for bar in bars if bar["t"] != "2026-10-07T19:55:00Z"]
        self.assertIn("TESTX:previous_adjusted_close_unavailable", self.collect()["errors"])

    def test_total_collection_budget_stops_further_requests(self):
        monotonic_now = [0.0]
        original = self.getter
        def slow(url, headers, params):
            response = original(url, headers, params)
            if url.endswith("/stocks/bars"):
                monotonic_now[0] = 61.0
            return response
        self.getter = slow
        with patch("equity_guard.providers.elapsed_time.monotonic", side_effect=lambda: monotonic_now[0]):
            result = self.collect()
        self.assertIn("TESTX:collection_budget_exceeded", result["errors"])
        self.assertEqual(DATA + "/v2/stocks/bars", self.calls[-1][0])
        self.assertNotIn("quote", result["securities"][0])

    def test_sec_pacing_applies_to_transient_retry_and_next_call(self):
        from urllib.error import HTTPError
        monotonic_now = [100.0]
        attempts = []
        class Response:
            status = 200
            def __init__(self): self.body = io.BytesIO(b'{"ok":true}')
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return self.body.read(n)
        def fake_open(*args, **kwargs):
            attempts.append(monotonic_now[0])
            if len(attempts) == 1:
                raise HTTPError("redacted", 429, "retry", {}, None)
            return Response()
        def fake_sleep(seconds): monotonic_now[0] += seconds
        with patch("equity_guard.providers._SEC_NEXT_REQUEST", 0), \
             patch("equity_guard.providers.elapsed_time.monotonic", side_effect=lambda: monotonic_now[0]), \
             patch("equity_guard.providers.elapsed_time.sleep", side_effect=fake_sleep), \
             patch("equity_guard.providers.build_opener") as opener:
            opener.return_value.open.side_effect = fake_open
            for _ in range(2):
                self.assertEqual({"ok": True}, get_json(SEC + "/submissions/CIK0000001234.json", {}, deadline=110))
        self.assertEqual([100.0, 100.5, 101.0], attempts)

    def test_deadline_prevents_transport_attempt(self):
        with patch("equity_guard.providers.elapsed_time.monotonic", return_value=60), \
             patch("equity_guard.providers.build_opener") as opener:
            with self.assertRaisesRegex(ProviderError, "collection_budget_exceeded"):
                get_json(DATA + "/v2/stocks/bars", {}, deadline=60)
            opener.return_value.open.assert_not_called()

    def test_news_discovery_excludes_stale_and_wrong_symbol_articles(self):
        self.context["symbols"][0].pop("news_review")
        original = self.payloads[DATA + "/v1beta1/news"]["news"][0]
        self.payloads[DATA + "/v1beta1/news"]["news"].extend([
            {**original, "id": 100, "created_at": "2026-10-06T13:00:00Z"},
            {**original, "id": 101, "symbols": ["OTHER"]},
        ])
        news = self.collect()["securities"][0]["news"]
        self.assertEqual("unknown", news["status"])
        self.assertEqual(["99"], [item["id"] for item in news["review_candidates"]])

    def test_url_allowlist_blocks_order_live_plaintext_and_custom_routes(self):
        for url in ("http://data.alpaca.markets/v2/stocks/bars", "https://api.alpaca.markets/v2/orders",
                    PAPER + "/v2/orders", DATA + "/v2/stocks/bars?secret=x", DATA + "/../v2/orders",
                    "https://data.alpaca.markets.evil.invalid/v2/stocks/bars", "https://x@data.alpaca.markets/v2/stocks/bars"):
            with self.assertRaises(ProviderError):
                get_json(url, {})

    def test_transport_is_get_and_redirect_rejected(self):
        from equity_guard.providers import _NoRedirect
        with self.assertRaisesRegex(ProviderError, "redirect_rejected"):
            _NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.invalid")
        class Response:
            status = 200
            consumed = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n):
                if self.consumed:
                    return b""
                self.consumed = True
                return b'{"ok":true}'
        with patch("equity_guard.providers.build_opener") as opener:
            opener.return_value.open.return_value = Response()
            self.assertEqual({"ok": True}, get_json(DATA + "/v2/stocks/snapshots", {"APCA-API-KEY-ID": "test"}, {"feed": "sip"}))
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual("GET", request.get_method())
            self.assertEqual(12, opener.return_value.open.call_args.kwargs["timeout"])

    def test_oversize_json_rejected(self):
        from equity_guard.providers import MAX_BODY
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return b"x" * (MAX_BODY + 1)
        with patch("equity_guard.providers.build_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaisesRegex(ProviderError, "response_too_large"):
                get_json(DATA + "/v2/stocks/snapshots", {})


if __name__ == "__main__":
    unittest.main()
