"""FICTIONAL fixtures only: no current market data or investment signals.

The public adapter may produce an unsized watch entry, never an AL/HAZIRLIK
trade draft. These tests exercise public-origin gates and retain PR #2 risks.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from itertools import chain, repeat
import json
import unittest
from unittest.mock import patch

from equity_guard import public_adapter
from test_engine import NOW, fictional_bundle, stamp


def fictional_public_inputs():
    bundle = fictional_bundle()
    security = bundle["securities"][0]
    filings = security.pop("filings")
    security["news"].update(sentiment="positive", symbol=security["symbol"],
                            original_source_reviewed=True)
    security["filings_review"] = {
        "status": "clear", "reviewed_at": stamp(),
        "reviewed_by": "FICTIONAL TEST reviewer", "flags": [],
    }
    observations = {
        "schema_version": 1,
        "provenance": {
            "provider": "licensed_public_export",
            "source_url": "https://example.test/FICTIONAL-export",
            "terms_url": "https://example.test/FICTIONAL-permission",
            "permission": "personal_automated_analysis",
            "permission_reviewed_at": stamp(),
            "verification": "operator_reviewed_original_export",
            "as_of": stamp(), "retrieved_at": stamp(), "delay_seconds": 0,
        },
        "market": bundle["market"], "securities": [security],
    }
    context = {key: bundle[key] for key in ("fx", "account", "costs")}
    return observations, context, filings


class FictionalSources:
    """Offline source results; every issuer and review is fictional."""

    def __init__(self, securities, filings):
        self.entries = [{"symbol": item["symbol"], "exchange": item["exchange"],
                         "kind": "unverified", "security_name": "FICTIONAL TEST stock"}
                        for item in securities]
        self.mapping = {item["symbol"]: {"exchange": item["exchange"], "cik": "0000000001"}
                        for item in securities}
        self.filings_result = deepcopy(filings)
        self.universe_status = "available"
        self.universe_errors = []
        self.map_status = "available"
        self.map_errors = []
        self.filings_calls = []
        self.map_calls = 0

    def universe(self):
        return {"status": self.universe_status, "entries": deepcopy(self.entries),
                "sources": [], "errors": self.universe_errors[:]}

    def symbol_map(self):
        self.map_calls += 1
        return {"status": self.map_status, "mapping": deepcopy(self.mapping),
                "sources": [], "errors": self.map_errors[:]}

    def filings(self, entry, now):
        self.filings_calls.append(deepcopy(entry))
        return deepcopy(self.filings_result)


def all_reasons(result):
    return " ".join(result["reasons"] + [
        reason for item in result["securities"] for reason in item["reasons"]])


class PublicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.observations, self.context, filings = fictional_public_inputs()
        self.sources = FictionalSources(self.observations["securities"], filings)

    @property
    def security(self):
        return self.observations["securities"][0]

    def scan(self, **kwargs):
        options = dict(observations=self.observations, context=self.context,
                       sources=self.sources, environ={}, now=NOW)
        options.update(kwargs)
        result = public_adapter.scan(**options)
        self.assertIn(result["decision"], {"İZLE", "PAS"})
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(result["candidates"], [])
        for item in result["watchlist"]:
            self.assertEqual(item["decision"], "İZLE")
            self.assertFalse(item["execution_enabled"])
            for field in ("quantity", "entry_limit_usd", "planned_stop_usd",
                          "entry_debit_try", "total_day_risk_try"):
                self.assertNotIn(field, item)
        json.dumps(result, allow_nan=False)
        return result

    def assert_pas(self, expected=None, **kwargs):
        result = self.scan(**kwargs)
        self.assertEqual(result["decision"], "PAS", all_reasons(result))
        self.assertEqual(result["watchlist"], [])
        if expected:
            self.assertIn(expected, all_reasons(result))
        return result

    def assert_watch(self, **kwargs):
        result = self.scan(**kwargs)
        self.assertEqual(result["decision"], "İZLE", all_reasons(result))
        self.assertEqual(result["data_status"], "OBSERVATIONS_ONLY")
        return result

    def test_complete_fictional_import_is_only_unsized_watch(self):
        result = self.assert_watch()
        self.assertEqual([item["symbol"] for item in result["watchlist"]], ["FICT"])
        self.assertEqual(result["quote_status"], "VERIFIED_EXECUTABLE_NBBO_UNAVAILABLE")
        self.assertEqual(result["securities"][0]["quote"]["status"], "UNAUTHENTICATED")
        self.assertEqual(result["coverage"]["sec_review_count"], 1)
        self.assertEqual(self.sources.filings_calls[0]["cik"], "0000000001")

    def test_all_fixed_financial_limits_are_preserved(self):
        self.assertEqual(self.assert_watch()["risk_limits"], {
            "capital_try": "50000", "position_cap_try": "12500",
            "planned_stop_pct": "3", "daily_loss_limit_try": "2500",
            "min_price_usd": "1", "max_price_usd": "5",
            "max_spread_usd": "0.05", "max_spread_pct": "2.500",
        })

    def test_source_time_delay_and_attestation_limit_are_reported(self):
        self.observations["provenance"].update(as_of=stamp(12), retrieved_at=stamp(2))
        source = self.assert_watch()["sources"][-1]
        self.assertEqual(source["provider"], "licensed_public_export")
        self.assertEqual(source["url"], "https://example.test/FICTIONAL-export")
        self.assertEqual(datetime.fromisoformat(source["source_timestamp"].replace("Z", "+00:00")),
                         NOW - timedelta(seconds=12))
        self.assertEqual(datetime.fromisoformat(source["retrieved_at"].replace("Z", "+00:00")),
                         NOW - timedelta(seconds=2))
        self.assertEqual(source["age_seconds"], 12)
        self.assertEqual(source["delay_seconds"], 0)
        self.assertEqual(source["trust_basis"],
                         "OPERATOR_REVIEWED_EXPORT_NOT_INDEPENDENTLY_AUTHENTICATED")

    def test_no_observations_is_data_unavailable_without_sec_lookup(self):
        result = self.assert_pas("PERMITTED_CURRENT_MARKET_OBSERVATIONS_UNAVAILABLE",
                                 observations=None)
        self.assertEqual(result["data_status"], "DATA_UNAVAILABLE")
        self.assertEqual(self.sources.map_calls, 0)
        self.assertEqual(self.sources.filings_calls, [])

    def test_missing_quote_can_only_support_unsized_watch(self):
        self.security["quote"] = None
        self.assertEqual(self.assert_watch()["securities"][0]["quote"]["status"], "MISSING")

    def test_stale_untimed_and_future_supplied_quotes_are_pas(self):
        for timestamp in (stamp(16), None, stamp(-1)):
            with self.subTest(timestamp=timestamp):
                self.security["quote"]["as_of"] = timestamp
                self.assert_pas("PUBLIC_QUOTE_STALE_OR_TIMESTAMP_UNVERIFIED")

    def test_unknown_delayed_or_boolean_delay_is_pas(self):
        for value in (None, 1, 900, True, "0", -1):
            with self.subTest(delay=value):
                self.observations["provenance"]["delay_seconds"] = value
                self.assert_pas("PUBLIC_DELAYED_OR_UNKNOWN_DATA")

    def test_rejected_stale_delayed_source_keeps_honest_time_delay_and_url_metadata(self):
        self.observations["provenance"].update(as_of=stamp(900), delay_seconds=900)
        source = self.assert_pas()["sources"][-1]
        self.assertFalse(source["accepted"])
        self.assertEqual(source["url"], "https://example.test/FICTIONAL-export")
        self.assertEqual(source["source_timestamp"], "2026-10-08T14:45:00Z")
        self.assertEqual(source["age_seconds"], 900)
        self.assertEqual(source["delay_seconds"], 900)
        self.assertIn("PUBLIC_DELAYED_OR_UNKNOWN_DATA", source["rejection_codes"])
        self.assertIn("PUBLIC_MARKET_DATA_STALE_OR_TIMESTAMP_UNVERIFIED", source["rejection_codes"])

    def test_stale_future_naive_and_missing_provenance_times_are_pas(self):
        for key, value in (("as_of", stamp(31)), ("retrieved_at", stamp(61)),
                           ("as_of", stamp(-1)), ("retrieved_at", stamp(-1)),
                           ("as_of", "2026-10-08T15:00:00"), ("as_of", None),
                           ("permission_reviewed_at", stamp(30 * 86400 + 1))):
            with self.subTest(key=key, value=value):
                observations = deepcopy(self.observations)
                observations["provenance"][key] = value
                self.assert_pas(observations=observations)

    def test_retrieval_cannot_precede_observation(self):
        self.observations["provenance"].update(as_of=stamp(1), retrieved_at=stamp(2))
        self.assert_pas("PUBLIC_RETRIEVAL_PRECEDES_OBSERVATION")

    def test_every_provenance_field_and_permission_are_required(self):
        for key in self.observations["provenance"]:
            with self.subTest(missing=key):
                observations = deepcopy(self.observations)
                del observations["provenance"][key]
                self.assert_pas("PUBLIC_PROVENANCE_REQUIRED", observations=observations)
        for key, value in (("permission", "public_page"), ("verification", "unreviewed"),
                           ("provider", "tradingview")):
            observations = deepcopy(self.observations)
            observations["provenance"][key] = value
            self.assert_pas(observations=observations)

    def test_unpermitted_sites_including_subdomains_are_blocked(self):
        for domain in ("tradingview.com", "www.tradingview.com", "nasdaq.com",
                       "www.nasdaq.com", "stooq.com", "stooq.pl"):
            for field in ("source_url", "terms_url"):
                with self.subTest(domain=domain, field=field):
                    observations = deepcopy(self.observations)
                    observations["provenance"][field] = "https://" + domain + "/export"
                    self.assert_pas("PUBLIC_SOURCE_USE_NOT_PERMITTED", observations=observations)

    def test_finviz_export_requires_matching_source_and_terms_domains(self):
        provenance = self.observations["provenance"]
        provenance.update(provider="finviz_elite_export", source_url="https://elite.finviz.com/export.ashx",
                          terms_url="https://finviz.com/terms.ashx")
        self.assert_watch()
        provenance["terms_url"] = "https://example.test/FICTIONAL-terms"
        self.assert_pas("PUBLIC_PROVIDER_SOURCE_MISMATCH")

    def test_provenance_urls_cannot_carry_credentials_queries_or_fragments(self):
        for value in ("http://example.test/data", "https://user:secret@example.test/data",
                      "https://example.test/data?token=FICTIONAL", "https://example.test/data#key",
                      "https://example.test:8080/data", "https://example.test/has space"):
            with self.subTest(value=value):
                self.observations["provenance"]["source_url"] = value
                result = self.assert_pas("PUBLIC_PROVENANCE_URL_INVALID")
                self.assertNotIn(value, json.dumps(result))

    def test_both_spread_bounds_and_crossing_are_enforced(self):
        for bid, ask, code in (("4", "4.051", "PUBLIC_SPREAD_LIMIT_EXCEEDED"),
                               ("1", "1.026", "PUBLIC_SPREAD_LIMIT_EXCEEDED"),
                               ("2.01", "2", "PUBLIC_CROSSED_QUOTE")):
            with self.subTest(bid=bid, ask=ask):
                self.security["quote"].update(bid=bid, ask=ask)
                self.assert_pas(code)

    def test_exact_spread_limits_are_inclusive(self):
        for bid, ask in (("2", "2.05"), ("1", "1.025")):
            with self.subTest(bid=bid, ask=ask):
                self.security["quote"].update(bid=bid, ask=ask)
                self.assert_watch()

    def test_malformed_quote_numbers_sizes_and_unknown_fields_are_pas(self):
        cases = [(field, value) for field in ("bid", "ask", "bid_size", "ask_size")
                 for value in (None, True, "NaN", "Infinity", 0, -1)]
        cases += [("ask_size", "0.5"), ("secret", "FICTIONAL")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                observations = deepcopy(self.observations)
                observations["securities"][0]["quote"][field] = value
                self.assert_pas("PUBLIC_QUOTE_INVALID", observations=observations)

    def test_price_range_is_checked_on_trade_and_present_ask(self):
        for field in ("trade", "quote"):
            for price in ("0.99", "5.01"):
                observations = deepcopy(self.observations)
                item = observations["securities"][0]
                item[field]["price" if field == "trade" else "ask"] = price
                self.assert_pas(observations=observations)

    def test_only_nasdaq_and_nyse_members_can_be_watched(self):
        self.security["exchange"] = "NYSE"
        self.sources.entries[0]["exchange"] = "NYSE"
        self.sources.mapping["FICT"]["exchange"] = "NYSE"
        self.assert_watch()
        for exchange in ("AMEX", "OTC", None):
            self.security["exchange"] = exchange
            self.assert_pas("PUBLIC_EXCHANGE_OUT_OF_SCOPE")

    def test_directory_membership_and_exchange_must_match(self):
        self.sources.entries = []
        self.assert_pas("PUBLIC_LISTING_NOT_IN_DISCOVERED_UNIVERSE")
        self.sources.entries = [{"symbol": "FICT", "exchange": "NYSE"}]
        self.assert_pas("PUBLIC_LISTING_NOT_IN_DISCOVERED_UNIVERSE")

    def test_common_stock_type_is_required_despite_listing(self):
        for kind in ("unverified", "etf", "adr", "warrant", None):
            with self.subTest(kind=kind):
                self.security["kind"] = kind
                self.assert_pas("COMMON_STOCK_REQUIRED")

    def test_volume_momentum_and_vwap_filters_remain_required(self):
        cases = (("day_volume", 999999), ("day_dollar_volume", "1999999"),
                 ("rvol_5m", "1.99"), ("return_5m_pct", "0.99"),
                 ("day_change_pct", "2.99"), ("vwap", "1.99"))
        for field, value in cases:
            with self.subTest(field=field):
                observations = deepcopy(self.observations)
                observations["securities"][0]["metrics"][field] = value
                self.assert_pas(observations=observations)
        self.assertEqual(self.sources.filings_calls, [], "cheap failures should not fetch SEC")

    def test_stale_trade_metrics_and_market_clock_are_pas(self):
        cases = (("trade", "as_of", 31), ("metrics", "as_of", 61),
                 ("metrics", "window_end", 331), ("news", "published_at", 86401))
        for section, key, age in cases:
            with self.subTest(section=section, key=key):
                observations = deepcopy(self.observations)
                observations["securities"][0][section][key] = stamp(age)
                self.assert_pas("STALE", observations=observations)
        self.observations["market"]["as_of"] = stamp(61)
        self.assert_pas("market.as_of:STALE")

    def test_news_must_be_positive_original_reviewed_and_symbol_specific(self):
        cases = (("sentiment", "negative"), ("sentiment", "unknown"),
                 ("symbol", "OTHER"), ("symbol", None),
                 ("original_source_reviewed", False), ("original_source_reviewed", "true"))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                observations = deepcopy(self.observations)
                observations["securities"][0]["news"][field] = value
                expected = ("PUBLIC_POSITIVE_CATALYST_UNVERIFIED" if field == "sentiment"
                            else "PUBLIC_NEWS_SOURCE_OR_SYMBOL_UNVERIFIED")
                self.assert_pas(expected, observations=observations)

    def test_news_urls_cannot_bypass_source_use_restrictions(self):
        for url in ("https://tradingview.com/news/FICTIONAL", "https://nasdaq.com/FICTIONAL",
                    "https://stooq.com/FICTIONAL", "http://example.test/FICTIONAL",
                    "https://example.test/FICTIONAL?token=FICTIONAL"):
            with self.subTest(url=url):
                self.security["news"]["url"] = url
                self.assert_pas()

    def test_original_news_materiality_and_review_chronology_are_preserved(self):
        for field, value in (("material", False), ("status", "unknown"),
                             ("reviewed_at", stamp(1201)), ("category", "rumor")):
            observations = deepcopy(self.observations)
            observations["securities"][0]["news"][field] = value
            self.assert_pas(observations=observations)

    def test_daily_loss_combines_realized_and_unrealized_after_fees(self):
        self.context["account"].update(realized_pnl_try="-2000", unrealized_pnl_try="-500")
        self.assert_pas("DAILY_LOSS_LIMIT_REACHED")
        self.context["account"].update(realized_pnl_try="500", unrealized_pnl_try="-1000")
        self.assert_watch()
        for scope in (None, "lifetime", "current_session_gross"):
            self.context["account"]["pnl_scope"] = scope
            self.assert_pas("CURRENT_SESSION_NET_FEES_REQUIRED")

    def test_existing_open_risk_and_entry_exit_costs_can_exhaust_daily_budget(self):
        self.context["account"].update(realized_pnl_try="-2000", open_risk_try="500")
        self.assert_pas("DAILY_RISK_BUDGET_EXHAUSTED")
        self.context["account"]["open_risk_try"] = "496"
        self.assert_pas("PUBLIC_CASH_OR_DAILY_RISK_BUDGET_EXHAUSTED")
        self.context["account"]["open_risk_try"] = "495"
        self.assert_watch()  # Unsized watch, not proof that one share is affordable.

    def test_same_symbol_lots_are_aggregated_at_position_cap(self):
        self.context["account"]["positions"] = [
            {"symbol": "FICT", "market_value_try": "6000"},
            {"symbol": "FICT", "market_value_try": "6500"},
        ]
        self.assert_pas("PUBLIC_EXISTING_CAPITAL_OR_POSITION_BUDGET_EXHAUSTED")
        self.context["account"]["positions"][1]["market_value_try"] = "6501"
        self.assert_pas("EXISTING_POSITION_CAP_EXCEEDED")

    def test_aggregate_capital_and_cash_with_costs_cannot_be_exhausted(self):
        self.context["account"]["positions"] = [
            {"symbol": "FICT" + str(index), "market_value_try": "12500"}
            for index in range(4)]
        self.assert_pas("PUBLIC_EXISTING_CAPITAL_OR_POSITION_BUDGET_EXHAUSTED")
        self.context["account"]["positions"] = []
        self.context["account"]["cash_try"] = "4"
        self.assert_pas("PUBLIC_CASH_OR_DAILY_RISK_BUDGET_EXHAUSTED")

    def test_entry_fee_cannot_consume_entire_remaining_position_or_capital_budget(self):
        self.context["account"]["positions"] = [{"symbol": "FICT", "market_value_try": "12498"}]
        self.assert_pas("PUBLIC_EXISTING_CAPITAL_OR_POSITION_BUDGET_EXHAUSTED")
        self.context["account"]["positions"] = [
            {"symbol": "OTHER" + str(index), "market_value_try": "12500"}
            for index in range(3)] + [{"symbol": "LAST", "market_value_try": "12498"}]
        self.assert_pas("PUBLIC_EXISTING_CAPITAL_OR_POSITION_BUDGET_EXHAUSTED")

    def test_pending_reservations_require_reconciliation(self):
        self.context["account"]["reserved_try"] = "1"
        self.assert_pas("PENDING_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION")

    def test_missing_stale_or_invalid_account_fx_and_costs_are_pas(self):
        for section in ("account", "fx", "costs"):
            with self.subTest(missing=section):
                context = deepcopy(self.context)
                del context[section]
                self.assert_pas(context=context)
        for section, key, age in (("account", "as_of", 301),
                                  ("account", "restrictions_verified_at", 301),
                                  ("fx", "as_of", 301), ("costs", "verified_at", 86401)):
            context = deepcopy(self.context)
            context[section][key] = stamp(age)
            self.assert_pas("STALE", context=context)
        for field in ("realized_pnl_try", "unrealized_pnl_try", "open_risk_try"):
            context = deepcopy(self.context)
            del context["account"][field]
            self.assert_pas(context=context)

    def test_unknown_flagged_or_stale_sec_review_blocks_watch(self):
        cases = (("status", "unknown"), ("status", "risk"),
                 ("flags", ["ATM"]), ("flags", ["DILUTION"]),
                 ("flags", ["FINANCING"]), ("flags", None),
                 ("checked_at", stamp(86401)), ("coverage_days", 364),
                 ("reviewed_by", ""))
        original = deepcopy(self.sources.filings_result)
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.sources.filings_result = {**original, field: value}
                self.assert_pas()

    def test_final_sec_review_cannot_be_replaced_with_imported_clear_claim(self):
        self.sources.filings_result = {"status": "unknown", "flags": [], "coverage_days": 0}
        self.assert_pas("FINANCING_DILUTION_REVIEW_NOT_CLEAR")
        self.assertEqual(len(self.sources.filings_calls), 1)

    def test_sec_mapping_unavailable_or_exchange_mismatch_is_pas(self):
        self.sources.map_status = "unavailable"
        self.sources.map_errors = ["sec_user_agent_required"]
        self.assert_pas("PUBLIC_SEC_SYMBOL_MAPPING_UNAVAILABLE")
        self.assertEqual(self.sources.filings_calls, [])
        self.sources.map_status, self.sources.map_errors = "available", []
        self.sources.mapping["FICT"]["exchange"] = "NYSE"
        self.assert_pas("PUBLIC_SEC_SYMBOL_EXCHANGE_MISMATCH")

    def test_universe_source_failure_blocks_all_candidates(self):
        self.sources.universe_status = "unavailable"
        self.sources.universe_errors = ["http_403"]
        self.assert_pas("PUBLIC_UNIVERSE_UNAVAILABLE")
        self.assertEqual(self.sources.map_calls, 0)
        self.assertEqual(self.sources.filings_calls, [])

    def test_source_exceptions_fail_closed_without_exception_text(self):
        for method in ("universe", "symbol_map", "filings"):
            with self.subTest(method=method), patch.object(
                    self.sources, method, side_effect=RuntimeError("FICTIONAL_PRIVATE_SENTINEL")):
                result = self.assert_pas("PUBLIC_SCAN_FAILED_CLOSED")
                self.assertNotIn("FICTIONAL_PRIVATE_SENTINEL", json.dumps(result))

    def test_at_most_twenty_shortlisted_sec_lookups_and_partial_coverage_reported(self):
        template = deepcopy(self.security)
        self.observations["securities"] = []
        for index in range(21):
            item = deepcopy(template)
            item["symbol"] = "FICT" + str(index)
            item["news"]["symbol"] = item["symbol"]
            self.observations["securities"].append(item)
        self.sources = FictionalSources(self.observations["securities"], self.sources.filings_result)
        self.sources.entries.append({"symbol": "UNSEEN", "exchange": "NYSE"})
        result = self.assert_watch()
        self.assertEqual(len(result["watchlist"]), 20)
        self.assertEqual(len(self.sources.filings_calls), 20)
        self.assertEqual(result["coverage"]["sec_review_count"], 20)
        self.assertEqual(result["coverage"]["unobserved_count"], 1)
        self.assertFalse(result["coverage"]["complete_market_scan"])
        self.assertEqual(result["securities"][-1]["decision"], "PAS")
        self.assertIn("PUBLIC_SEC_REVIEW_LIMIT_NOT_EVALUATED", result["securities"][-1]["reasons"])

    def test_stale_data_after_slow_sec_collection_discards_earlier_watch_items(self):
        with patch.object(public_adapter, "_now",
                          side_effect=chain((NOW, NOW), repeat(NOW + timedelta(seconds=31)))):
            self.assert_pas("PUBLIC_DATA_EXPIRED_DURING_COLLECTION", now=None)

    def test_trade_age_29_seconds_expires_at_final_pass_after_two_seconds(self):
        self.security["trade"]["as_of"] = stamp(29)
        with patch.object(public_adapter, "_now",
                          side_effect=chain((NOW, NOW), repeat(NOW + timedelta(seconds=2)))):
            result = self.assert_pas("securities[0].trade.as_of:STALE", now=None)
        self.assertNotIn("PUBLIC_DATA_EXPIRED_DURING_COLLECTION", result["reasons"])

    def test_final_pass_rechecks_earlier_trade_even_when_global_provenance_is_fresh(self):
        self.security["quote"] = None
        second = deepcopy(self.security)
        second["symbol"] = "SECOND"
        second["news"]["symbol"] = "SECOND"
        self.observations["securities"].append(second)
        self.security["trade"]["as_of"] = stamp(20)
        self.sources = FictionalSources(self.observations["securities"], self.sources.filings_result)
        end = NOW + timedelta(seconds=11)
        with patch.object(public_adapter, "_now", side_effect=chain((NOW, NOW), repeat(end))):
            result = self.assert_watch(now=None)
        self.assertEqual([item["symbol"] for item in result["watchlist"]], ["SECOND"])
        self.assertEqual(result["securities"][0]["decision"], "PAS")
        self.assertIn("securities[0].trade.as_of:STALE", result["securities"][0]["reasons"])

    def test_malformed_shapes_duplicate_symbols_and_override_fields_are_pas(self):
        for value in (None, [], True, 1, "bad", {}, {"securities": [None]}):
            with self.subTest(value=value):
                self.assert_pas(observations=value)
        self.observations["securities"].append(deepcopy(self.security))
        self.assert_pas("PUBLIC_SECURITY_SCHEMA_OR_DUPLICATE_INVALID")
        self.observations["securities"].pop()
        self.context["position_cap_try"] = "999999"
        self.assert_pas("PUBLIC_CONTEXT_SCHEMA_INVALID")

    def test_scan_does_not_mutate_inputs(self):
        before = deepcopy((self.observations, self.context))
        self.assert_watch()
        self.assertEqual((self.observations, self.context), before)

    def test_scan_reads_no_files_creates_no_process_and_connects_to_no_manager(self):
        with patch("builtins.open") as file_open, patch("os.open") as os_open, \
                patch("subprocess.Popen") as process, patch("socket.socket") as network:
            self.assert_watch()
        file_open.assert_not_called()
        os_open.assert_not_called()
        process.assert_not_called()
        network.assert_not_called()

    def test_default_source_constructor_reads_only_sec_user_agent(self):
        class GuardedEnvironment:
            def __init__(self):
                self.reads = []

            def get(self, key, default=None):
                if key != "SEC_USER_AGENT":
                    raise AssertionError("secret environment read attempted")
                self.reads.append(key)
                return ""

        environ = GuardedEnvironment()
        unavailable = {"status": "unavailable", "entries": [], "sources": [],
                       "errors": ["network_unavailable"]}
        with patch("equity_guard.public_sources.PublicTransport"), \
                patch("equity_guard.public_sources.PublicSources.universe", return_value=unavailable):
            self.assert_pas(sources=None, environ=environ)
        self.assertEqual(environ.reads, ["SEC_USER_AGENT"])


if __name__ == "__main__":
    unittest.main()
