"""Offline, FICTIONAL market observations; never current investment signals."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from equity_guard import public_adapter, public_live
from equity_guard.fintable import FintableSource


NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (NOW - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def fictional_quote(symbol="FICT"):
    return {"symbol": symbol, "price": "2.2", "previous_close": "2.0",
            "volume": 1500000, "currency": "USD", "trading_day": "2026-10-08",
            "as_of": stamp(), "feed": "iex", "errors": []}


def fictional_bar(seconds, close, volume):
    return {"timestamp": stamp(seconds), "date": "2026-10-08", "open": close,
            "high": close, "low": close, "close": close, "volume": volume,
            "vwap": close, "trade_count": 5}


def fictional_history(symbol="FICT"):
    return {"symbol": symbol, "timeframe": "5min", "currency": "USD",
            "feed": "iex", "bars": [fictional_bar(600, "2", 100),
                                       fictional_bar(300, "2.2", 300)]}


def fictional_ranking(symbols=("FICT",), *, age=0, delay=0):
    """Explicitly fictional operator-reviewed local export; no live quotes."""
    return {"schema_version": 1, "source": {
        "provider": "licensed_public_export",
        "url": "https://fictional.example/export",
        "terms_url": "https://fictional.example/terms",
        "permission": "personal_automated_analysis",
        "permission_reviewed_at": stamp(),
        "verification": "operator_reviewed_original_export",
        "retrieved_at": stamp(), "delay_seconds": delay,
        "volume_scope": "consolidated_us",
        "volume_basis": "session_cumulative_shares",
    }, "records": [
        {"symbol": symbol, "exchange": "NASDAQ", "price_usd": "2.%02d" % (index + 1),
         "previous_close_usd": "2", "volume_shares": (index + 1) * 100000,
         "as_of": stamp(age), "session_date": "2026-10-08"}
        for index, symbol in enumerate(symbols)
    ]}


class FictionalDirectory:
    def __init__(self, symbols=("FICT",)):
        symbols = tuple(symbols)
        self.entries = [{"symbol": s, "exchange": "NASDAQ", "kind": "unverified"}
                        for s in symbols]
        self.status = "available"
        self.errors = []
        self.mapping = {s: {"exchange": "NASDAQ", "cik": "0000000001"} for s in symbols}
        self.mapping_status = "available"
        self.universe_calls = 0
        self.map_calls = 0
        self.filings_calls = []
        self.filing = {"status": "unknown", "flags": [], "coverage_days": 365,
                       "source_url": "https://data.sec.gov/submissions/CIK0000000001.json"}

    def universe(self):
        self.universe_calls += 1
        return {"status": self.status, "entries": deepcopy(self.entries),
                "sources": [], "errors": self.errors[:]}

    def symbol_map(self):
        self.map_calls += 1
        return {"status": self.mapping_status, "mapping": deepcopy(self.mapping),
                "sources": [], "errors": []}

    def filings(self, entry, now):
        self.filings_calls.append(deepcopy(entry))
        return deepcopy(self.filing)


class FictionalMarket:
    def __init__(self, symbols=("FICT",)):
        self.quotes = {s: fictional_quote(s) for s in symbols}
        self.histories = {s: fictional_history(s) for s in symbols}
        self.prices_calls = []
        self.history_calls = []
        self.errors = []
        self.history_errors = []

    def prices(self, symbols):
        self.prices_calls.append(list(symbols))
        return {"status": "available", "quotes": deepcopy({s: self.quotes[s]
                for s in symbols if s in self.quotes}),
                "sources": [], "errors": self.errors[:]}

    def history(self, symbol, day):
        self.history_calls.append((symbol, day))
        return {"status": "available", "data": deepcopy(self.histories.get(symbol)),
                "sources": [], "errors": self.history_errors[:]}


class PublicLiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = FictionalDirectory()
        self.market = FictionalMarket()

    def scan(self, **changes):
        options = {"provider": "fintable", "sources": self.directory,
                   "market_source": self.market, "now": NOW, "environ": {},
                   "symbols": ["FICT"]}
        options.update(changes)
        result = public_adapter.scan(**options)
        self.assertEqual(result["decision"], "PAS")
        self.assertEqual(result["data_status"], "DATA_UNAVAILABLE")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["watchlist"], [])
        self.assertFalse(result["execution_enabled"])
        for row in result["securities"]:
            self.assertEqual(row["decision"], "PAS")
            self.assertFalse(row["execution_enabled"])
            for field in ("quantity", "entry_limit_usd", "planned_stop_usd", "order"):
                self.assertNotIn(field, row)
        json.dumps(result, allow_nan=False)
        return result

    def row(self, **changes):
        result = self.scan(**changes)
        self.assertEqual(len(result["securities"]), 1, result)
        return result["securities"][0]

    def test_fresh_iex_reports_observations_and_explicit_unavailable_evidence(self):
        row = self.row()
        self.assertEqual(row["last_price_usd"], "2.2")
        self.assertEqual(row["volume"], 1500000)
        self.assertEqual(row["volume_scope"], "IEX_ONLY")
        self.assertEqual(row["source_timestamp"], stamp())
        self.assertEqual(row["age_seconds"], 0)
        self.assertIsNone(row["delay_seconds"])
        self.assertEqual(row["quote"]["status"], "DATA_UNAVAILABLE")
        self.assertIsNone(row["quote"]["spread_usd"])
        self.assertIsNone(row["news"]["headline"])
        self.assertEqual(row["sec"]["status"], "unknown")
        self.assertIn("SPREAD_UNAVAILABLE", row["reasons"])
        self.assertIn("NEWS_CATALYST_UNVERIFIED", row["reasons"])
        self.assertIn("SEC_FULL_DOCUMENT_REVIEW_REQUIRED", row["reasons"])
        self.assertEqual(row["day_change_pct"], "10.0")

    def test_consecutive_volume_ratio_is_never_promoted_to_rvol(self):
        metrics = self.row()["metrics"]
        self.assertEqual(metrics["return_5m_pct"], "10.0")
        self.assertEqual(metrics["volume_ratio_previous_5m"], "3")
        self.assertEqual(metrics["window_volume"], 300)
        self.assertEqual(metrics["previous_window_volume"], 100)
        self.assertIsNone(metrics["rvol_5m"])
        self.assertEqual(metrics["feed"], "iex")
        self.assertIn("not_session_adjusted_RVOL", metrics["basis"])

    def test_explicit_selection_has_hard_twenty_symbol_limit(self):
        symbols = ["FICT" + str(n).zfill(2) for n in range(25)]
        self.directory = FictionalDirectory(reversed(symbols))
        self.market = FictionalMarket(symbols[:20])
        result = self.scan(symbols=symbols[:20],
                           environ={"SEC_USER_AGENT": "FICTIONAL fixture contact"})
        self.assertEqual(self.market.prices_calls, [symbols[:20]])
        self.assertEqual(len(self.market.history_calls), 20)
        self.assertEqual(len(self.directory.filings_calls), 20)
        self.assertEqual(len(result["securities"]), 20)
        self.assertEqual(result["coverage"]["requested_count"], 20)
        self.assertEqual(result["coverage"]["unobserved_count"], 5)
        self.assertFalse(result["coverage"]["complete_market_scan"])
        self.assertEqual(result["coverage"]["selection"], "explicit_symbols")

    def test_missing_ranking_and_manual_selection_is_pas_without_network(self):
        result = self.scan(symbols=None)
        self.assertIn("RANKING_INPUT_REQUIRED", result["reasons"])
        self.assertEqual(result["securities"], [])
        self.assertEqual(result["coverage"]["requested_count"], 0)
        self.assertEqual(self.directory.universe_calls, 0)
        self.assertEqual(self.directory.map_calls, 0)
        self.assertEqual(self.directory.filings_calls, [])
        self.assertEqual(self.market.prices_calls, [])
        self.assertEqual(self.market.history_calls, [])

    def test_explicit_selection_preserves_user_symbols_only(self):
        self.directory = FictionalDirectory(("FICT", "TEST"))
        self.market = FictionalMarket(("TEST",))
        result = self.scan(symbols=["TEST"])
        self.assertEqual(self.market.prices_calls, [["TEST"]])
        self.assertEqual([r["symbol"] for r in result["securities"]], ["TEST"])
        self.assertEqual(result["coverage"]["selection"], "explicit_symbols")

    def test_ranking_requests_best_twenty_instead_of_alphabetic_first_twenty(self):
        symbols = ["A%02d" % index for index in range(24)] + ["ZHIGH"]
        self.directory = FictionalDirectory(symbols)
        self.market = FictionalMarket(symbols)
        ranked = fictional_ranking(symbols)
        expected = list(reversed(symbols))[:20]
        result = self.scan(symbols=None, ranking_input=ranked,
                           environ={"SEC_USER_AGENT": "FICTIONAL fixture contact"})
        self.assertEqual(self.market.prices_calls, [expected])
        self.assertEqual([symbol for symbol, day in self.market.history_calls], expected)
        self.assertEqual([row["symbol"] for row in self.directory.filings_calls], expected)
        self.assertEqual([row["symbol"] for row in result["securities"]], expected)
        self.assertEqual(result["coverage"]["requested_count"], 20)
        self.assertEqual(result["coverage"]["unobserved_count"], 5)
        self.assertFalse(result["coverage"]["complete_market_scan"])
        self.assertEqual(result["coverage"]["selection"], "volume_momentum_ranked_local_export")
        self.assertEqual(result["ranking"]["selected_symbols"], expected)
        self.assertEqual([row["symbol"] for row in result["research_priority"]], expected)
        self.assertEqual(expected[0], "ZHIGH")
        self.assertNotIn("A00", result["coverage"]["selected_symbols"])
        for row in result["research_priority"]:
            self.assertEqual(row["decision"], "PAS")
            self.assertFalse(row["execution_enabled"])
            self.assertFalse(row["executable_nbbo"])

    def test_delayed_ranking_keeps_source_and_delay_separate_from_market_quote(self):
        result = self.scan(symbols=None, ranking_input=fictional_ranking(age=600, delay=600))
        priority, = result["research_priority"]
        source = result["ranking"]["source"]
        self.assertTrue(priority["delayed"])
        self.assertEqual(priority["declared_delay_seconds"], 600)
        self.assertEqual(priority["data_age_seconds"], 600)
        self.assertEqual(priority["volume_scope"], "consolidated_us")
        self.assertEqual(source["url"], "https://fictional.example/export")
        self.assertEqual(source["provider"], "licensed_public_export")
        self.assertEqual(source["retrieved_at"], stamp())
        self.assertTrue(source["declared_delayed"])
        self.assertEqual(source["assurance"], "OPERATOR_ATTESTATION_NOT_INDEPENDENTLY_VERIFIED")
        row, = result["securities"]
        self.assertEqual(row["source_timestamp"], stamp())
        self.assertIsNone(row["delay_seconds"])
        self.assertEqual(row["volume_scope"], "IEX_ONLY")
        self.assertEqual(row["quote"]["status"], "DATA_UNAVAILABLE")
        self.assertIn("SPREAD_UNAVAILABLE", row["reasons"])
        self.assertIn("NEWS_CATALYST_UNVERIFIED", row["reasons"])

    def test_invalid_or_stale_ranking_never_falls_back_to_directory_order(self):
        stale = fictional_ranking(age=901)
        unreviewed = fictional_ranking()
        unreviewed["source"]["permission"] = "unknown"
        for ranking_input in ({}, stale, unreviewed):
            with self.subTest(ranking_input=ranking_input):
                result = self.scan(symbols=None, ranking_input=ranking_input)
                self.assertEqual(result["securities"], [])
                self.assertEqual(result["research_priority"], [])
                self.assertEqual(result["coverage"]["requested_count"], 0)
                self.assertEqual(result["ranking"]["status"], "unavailable")
        self.assertEqual(self.market.prices_calls, [])
        self.assertEqual(self.market.history_calls, [])
        self.assertEqual(self.directory.map_calls, 0)
        self.assertEqual(self.directory.filings_calls, [])

    def test_manual_symbols_cannot_override_or_mix_with_ranking_selection(self):
        result = self.scan(symbols=["FICT"], ranking_input=fictional_ranking())
        self.assertEqual(result["securities"], [])
        self.assertEqual(result["coverage"]["requested_count"], 0)
        self.assertEqual(self.directory.universe_calls, 0)
        self.assertEqual(self.market.prices_calls, [])

    def test_ranked_mode_preserves_fixed_risk_limits_without_issuing_trade_plan(self):
        result = self.scan(symbols=None, ranking_input=fictional_ranking())
        self.assertEqual(result["risk_limits"], {
            "capital_try": "50000", "position_cap_try": "12500",
            "planned_stop_pct": "3", "daily_loss_limit_try": "2500",
            "min_price_usd": "1", "max_price_usd": "5",
            "max_spread_usd": "0.05", "max_spread_pct": "2.500",
        })
        for row in result["research_priority"]:
            for field in ("quantity", "entry_limit_usd", "planned_stop_usd", "order"):
                self.assertNotIn(field, row)

    def test_expiring_selected_rows_are_dropped_without_refilling_symbol_budget(self):
        symbols = ["A%02d" % index for index in range(21)]
        self.directory = FictionalDirectory(symbols)
        self.market = FictionalMarket(symbols)
        ranking_input = fictional_ranking(symbols, age=890, delay=600)
        # The lowest-ranked row will remain current after every selected row
        # expires. It must never trigger a twenty-first market/SEC lookup.
        ranking_input["records"][0]["as_of"] = stamp(850)
        expected = list(reversed(symbols))[:20]

        class CollectionClock(datetime):
            current = NOW

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz) if tz else cls.current

        original_prices = self.market.prices

        def completing_prices(selected):
            fetched = original_prices(selected)
            CollectionClock.current = NOW + timedelta(seconds=11)
            return fetched

        with patch.object(public_live, "datetime", CollectionClock), \
                patch.object(self.market, "prices", side_effect=completing_prices):
            result = self.scan(symbols=None, ranking_input=ranking_input, now=None,
                               environ={"SEC_USER_AGENT": "FICTIONAL fixture contact"})
        self.assertEqual(self.market.prices_calls, [expected])
        self.assertEqual([symbol for symbol, day in self.market.history_calls], expected)
        self.assertEqual([row["symbol"] for row in self.directory.filings_calls], expected)
        self.assertEqual(result["coverage"]["selected_symbols"], expected)
        self.assertEqual(result["coverage"]["requested_count"], 20)
        self.assertEqual(result["research_priority"], [])
        self.assertEqual(result["ranking"]["research_priority"], [])
        self.assertEqual(result["ranking"]["selected_symbols"], [])
        self.assertEqual(result["ranking"]["status"], "unavailable")
        self.assertIn("RANKING_EXPIRED_DURING_COLLECTION", result["ranking"]["errors"])
        # Source audit metadata must use the same final clock, not scan start.
        ranked_source = next(s for s in result["sources"] if s.get("provider") == "licensed_public_export")
        self.assertEqual(ranked_source["retrieval_age_seconds"], 11)

    def test_invalid_duplicate_or_over_twenty_selection_makes_no_market_calls(self):
        for symbols in ([], "FICT", ["FICT", "FICT"], ["fict"], ["../FICT"],
                        [None], ["FICT" + str(n) for n in range(21)]):
            with self.subTest(symbols=symbols):
                result = self.scan(symbols=symbols)
                self.assertIn("PUBLIC_SYMBOL_SELECTION_INVALID", result["reasons"])
        self.assertEqual(self.market.prices_calls, [])

    def test_undiscovered_symbol_and_unavailable_directory_never_request_prices(self):
        result = self.scan(symbols=["TEST"])
        self.assertIn("PUBLIC_LISTING_NOT_IN_DISCOVERED_UNIVERSE", result["reasons"])
        self.directory.status = "unavailable"
        self.directory.errors = ["http_403"]
        result = self.scan()
        self.assertIn("http_403", result["reasons"])
        self.assertIn("PUBLIC_UNIVERSE_UNAVAILABLE", result["reasons"])
        self.assertEqual(self.market.prices_calls, [])

    def test_stale_missing_future_naive_time_is_honestly_reported(self):
        for timestamp in (stamp(31), stamp(-1), None, "2026-10-08T15:00:00"):
            with self.subTest(timestamp=timestamp):
                self.market.quotes["FICT"]["as_of"] = timestamp
                row = self.row()
                self.assertIn("PUBLIC_MARKET_DATA_STALE_OR_TIMESTAMP_UNVERIFIED", row["reasons"])
                if timestamp == stamp(31):
                    self.assertEqual(row["source_timestamp"], timestamp)
                    self.assertEqual(row["age_seconds"], 31)

    def test_volume_session_is_separate_from_fresh_price_timestamp(self):
        self.market.quotes["FICT"]["trading_day"] = "2026-10-07"
        row = self.row()
        self.assertEqual(row["source_timestamp"], stamp())
        self.assertIn("VOLUME_SESSION_STALE_OR_UNVERIFIED", row["reasons"])

    def test_missing_price_does_not_fallback_to_history_or_zero(self):
        self.market.quotes = {}
        row = self.row()
        self.assertIsNone(row["last_price_usd"])
        self.assertIsNone(row["volume"])
        self.assertIsNone(row["source_timestamp"])
        self.assertEqual(self.market.history_calls, [])
        self.assertEqual(self.directory.filings_calls, [])
        self.assertIn("PRICE_DATA_UNAVAILABLE", row["reasons"])

    def test_out_of_price_range_skips_history_and_sec(self):
        for price in ("0.99", "5.0001"):
            with self.subTest(price=price):
                self.market.quotes["FICT"]["price"] = price
                row = self.row(environ={"SEC_USER_AGENT": "FICTIONAL contact"})
                self.assertFalse(row["in_price_range"])
                self.assertIn("PRICE_OUTSIDE_1_5_USD_OR_UNAVAILABLE", row["reasons"])
        self.assertEqual(self.market.history_calls, [])
        self.assertEqual(self.directory.map_calls, 0)

    def test_price_filter_includes_one_and_five_dollars(self):
        for price in ("1", "5"):
            with self.subTest(price=price):
                self.market.quotes["FICT"]["price"] = price
                self.assertTrue(self.row()["in_price_range"])

    def test_missing_previous_close_does_not_invent_daily_return(self):
        self.market.quotes["FICT"]["previous_close"] = None
        self.assertIsNone(self.row()["day_change_pct"])

    def test_missing_sec_identity_does_not_attempt_sec_or_claim_clear(self):
        row = self.row(environ={"UNRELATED_SECRET": "FICTIONAL_DO_NOT_LEAK"})
        self.assertEqual(self.directory.map_calls, 0)
        self.assertEqual(self.directory.filings_calls, [])
        self.assertEqual(row["sec"]["error"], "sec_user_agent_required")
        self.assertNotIn("FICTIONAL_DO_NOT_LEAK", json.dumps(row))

    def test_sec_metadata_unknown_remains_unknown_despite_complete_coverage(self):
        row = self.row(environ={"SEC_USER_AGENT": "FICTIONAL contact"})
        self.assertEqual(row["sec"]["coverage_days"], 365)
        self.assertEqual(row["sec"]["status"], "unknown")
        self.assertIn("SEC_FULL_DOCUMENT_REVIEW_REQUIRED", row["reasons"])
        self.assertEqual(self.directory.filings_calls[0], {"symbol": "FICT", "cik": "0000000001"})

    def test_sec_financing_flags_are_retained_and_cannot_authorize_watch(self):
        self.directory.filing.update(flags=["SEC_FINANCING_FORM_S-3"], status="flagged")
        row = self.row(environ={"SEC_USER_AGENT": "FICTIONAL contact"})
        self.assertEqual(row["sec"]["flags"], ["SEC_FINANCING_FORM_S-3"])
        self.assertIn("SEC_FINANCING_RISK_OR_REVIEW_FLAG", row["reasons"])

    def test_wrong_sec_exchange_blocks_issuer_request(self):
        self.directory.mapping["FICT"]["exchange"] = "NYSE"
        row = self.row(environ={"SEC_USER_AGENT": "FICTIONAL contact"})
        self.assertEqual(self.directory.filings_calls, [])
        self.assertEqual(row["sec"]["error"], "PUBLIC_SEC_SYMBOL_EXCHANGE_MISMATCH")

    def test_missing_sec_map_does_not_disappear_from_report(self):
        self.directory.mapping_status = "unavailable"
        row = self.row(environ={"SEC_USER_AGENT": "FICTIONAL contact"})
        self.assertEqual(row["sec"]["error"], "PUBLIC_SEC_SYMBOL_MAPPING_UNAVAILABLE")
        self.assertEqual(self.directory.filings_calls, [])

    def test_incomplete_latest_bar_is_excluded_even_when_it_has_huge_volume(self):
        self.market.histories["FICT"]["bars"].append(fictional_bar(0, "4.9", 100000000))
        metrics = self.row()["metrics"]
        self.assertEqual(metrics["window_end"], stamp())
        self.assertEqual(metrics["window_volume"], 300)
        self.assertEqual(metrics["return_5m_pct"], "10.0")

    def test_history_from_wrong_feed_is_never_combined_with_current_quote(self):
        self.market.histories["FICT"]["feed"] = "sip"
        metrics = self.row()["metrics"]
        self.assertEqual(metrics["status"], "DATA_UNAVAILABLE")
        self.assertIsNone(metrics["return_5m_pct"])
        self.assertIn("MISMATCH", metrics["reason"])

    def test_stale_nonconsecutive_prior_session_bars_do_not_form_momentum(self):
        for bars in (
                [fictional_bar(1200, "2", 100), fictional_bar(900, "2.2", 300)],
                [fictional_bar(900, "2", 100), fictional_bar(300, "2.2", 300)],
                [fictional_bar(86400 + 600, "2", 100), fictional_bar(86400 + 300, "2.2", 300)]):
            with self.subTest(bars=bars):
                self.market.histories["FICT"]["bars"] = bars
                metrics = self.row()["metrics"]
                self.assertEqual(metrics["status"], "DATA_UNAVAILABLE")
                self.assertIsNone(metrics["return_5m_pct"])
                self.assertIsNone(metrics["volume_ratio_previous_5m"])

    def test_zero_previous_volume_has_no_infinite_ratio(self):
        self.market.histories["FICT"]["bars"][0]["volume"] = 0
        metrics = self.row()["metrics"]
        self.assertIsNone(metrics["volume_ratio_previous_5m"])
        self.assertEqual(metrics["return_5m_pct"], "10.0")

    def test_collection_end_rechecks_snapshot_age(self):
        class AdvancingClock(datetime):
            calls = 0

            @classmethod
            def now(cls, tz=None):
                cls.calls += 1
                value = NOW if cls.calls < 3 else NOW + timedelta(seconds=31)
                return value.astimezone(tz) if tz else value

        with patch.object(public_live, "datetime", AdvancingClock):
            row = self.row(now=None)
        self.assertEqual(row["age_seconds"], 31)
        self.assertIn("PUBLIC_MARKET_DATA_STALE_OR_TIMESTAMP_UNVERIFIED", row["reasons"])

    def test_later_sec_completion_cannot_complete_a_previously_incomplete_bar(self):
        self.market.histories["FICT"]["bars"].append(fictional_bar(0, "4.9", 100000000))

        class CrossedBarClock(datetime):
            calls = 0

            @classmethod
            def now(cls, tz=None):
                cls.calls += 1
                # collect start, history day, and history receipt precede the
                # boundary; SEC then pushes collection to the next bar end.
                value = NOW if cls.calls <= 3 else NOW + timedelta(minutes=5)
                return value.astimezone(tz) if tz else value

        with patch.object(public_live, "datetime", CrossedBarClock):
            row = self.row(now=None, environ={"SEC_USER_AGENT": "FICTIONAL contact"})
        self.assertEqual(row["metrics"]["window_end"], stamp())
        self.assertEqual(row["metrics"]["window_volume"], 300)
        self.assertEqual(row["metrics"]["return_5m_pct"], "10.0")

    def test_weekend_bars_are_not_a_verified_regular_session(self):
        weekend = NOW + timedelta(days=2)
        data = fictional_history()
        for bar in data["bars"]:
            bar["timestamp"] = (datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00")) + timedelta(days=2)).isoformat()
            bar["date"] = "2026-10-10"
        metrics = public_live._metrics(data, "iex", weekend)
        self.assertEqual(metrics["status"], "DATA_UNAVAILABLE")
        self.assertIsNone(metrics["return_5m_pct"])

    def test_default_sources_share_one_transport_budget(self):
        self.directory.transport = object()
        with patch("equity_guard.public_sources.PublicSources", return_value=self.directory), \
                patch("equity_guard.fintable.FintableSource", return_value=self.market) as factory:
            self.scan(sources=None, market_source=None)
        self.assertIs(factory.call_args.kwargs["transport"], self.directory.transport)

    def test_implicit_environment_reads_only_sec_contact(self):
        with patch.object(public_live.os, "environ", {"SEC_USER_AGENT": "FICTIONAL contact",
                                                   "TM_AUTH_KEY_HEX": "FICTIONAL_SECRET"}):
            row = self.row(environ=None)
        self.assertEqual(len(self.directory.filings_calls), 1)
        self.assertNotIn("FICTIONAL_SECRET", json.dumps(row))

    def test_unexpected_exception_has_fixed_error_without_source_or_secret_contents(self):
        with patch.object(self.market, "prices", side_effect=RuntimeError("FICTIONAL_SECRET_DETAIL")):
            result = self.scan()
        self.assertEqual(result["reasons"], ["PUBLIC_SCAN_FAILED_CLOSED"])
        self.assertNotIn("FICTIONAL_SECRET_DETAIL", json.dumps(result))

    def test_live_provider_rejects_mixed_manual_attestations(self):
        for changes in ({"observations": {}}, {"context": {}}, {"provider": "unknown"}):
            with self.subTest(changes=changes):
                result = self.scan(**changes)
                self.assertEqual(result["reasons"], ["PUBLIC_SCAN_FAILED_CLOSED"])
        self.assertEqual(self.market.prices_calls, [])

    def test_real_provider_parser_and_live_adapter_integration_retains_source_scope(self):
        quote = fictional_quote()
        quote.pop("errors")

        class OfflineTransport:
            def get(self, url, headers):
                self.last_url = url
                payload = fictional_history() if "/history?" in url else [quote]
                return json.dumps({"data": payload}).encode("utf-8")

        row = self.row(market_source=FintableSource(transport=OfflineTransport(), now=NOW))
        self.assertEqual(row["last_price_usd"], "2.2")
        self.assertEqual(row["metrics"]["window_volume"], 300)
        self.assertEqual(row["volume_scope"], "IEX_ONLY")
        self.assertIn("FINTABLE_DELAY_AND_PRICE_FALLBACK_NOT_VERIFIED", row["reasons"])


if __name__ == "__main__":
    unittest.main()
