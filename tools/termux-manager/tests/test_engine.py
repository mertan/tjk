"""All symbols, quotes, FX rates, news and account values below are FICTIONAL.

Fixtures exercise deterministic calculations only; they are not market data,
trade recommendations or assertions about any real security or account.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from equity_guard.engine import evaluate


NOW = datetime(2026, 10, 8, 15, 0, 0, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def fictional_bundle():
    """FICTIONAL unit-test fixture. 'live' exercises gates, not a data source."""
    return {
        "mode": "live",
        "errors": [],
        "quote_source": {"provider": "alpaca", "feed": "sip", "realtime": True},
        "market": {"is_open": True, "as_of": stamp(), "session_date": "2026-10-08",
                   "source": "FICTIONAL TEST market clock"},
        "fx": {"usdtry_ask": "40", "usdtry_bid": "39.9", "as_of": stamp(),
               "source": "FICTIONAL TEST executable FX quotes"},
        "account": {
            "as_of": stamp(), "session_date": "2026-10-08",
            "source": "FICTIONAL TEST broker state", "cash_try": "50000",
            "pnl_scope": "current_session_net_fees",
            "realized_pnl_try": "0", "unrealized_pnl_try": "0", "open_risk_try": "0",
            "reserved_try": "0", "positions": [], "trading_allowed": True,
            "restrictions_verified_at": stamp(),
        },
        "costs": {
            "entry_fee_try": "2", "exit_fee_try": "2", "slippage_pct": "0.25",
            "source": "FICTIONAL TEST current applicable fee schedule",
            "verified_at": stamp(), "sub_dollar_exit_covered": True,
        },
        "securities": [{
            "symbol": "FICT", "exchange": "NASDAQ", "kind": "stock",
            "broker_available": True, "broker_verified_at": stamp(),
            "quote": {"bid": "1.96", "ask": "2", "bid_size": 100000,
                      "ask_size": 100000, "as_of": stamp()},
            "trade": {"price": "1.99", "as_of": stamp()},
            "metrics": {"as_of": stamp(), "window_end": stamp(),
                        "day_volume": 1500000, "day_dollar_volume": 3000000,
                        "rvol_5m": "3", "return_5m_pct": "2", "day_change_pct": "5",
                        "vwap": "1.90"},
            "news": {"status": "verified", "published_at": stamp(1200),
                     "reviewed_at": stamp(600), "url": "https://example.test/FICTIONAL-news",
                     "title": "FICTIONAL catalyst for unit testing only",
                     "category": "earnings", "material": True},
            "filings": {"status": "clear", "checked_at": stamp(),
                        "source_url": "https://example.test/FICTIONAL-filings",
                        "coverage_days": 365, "reviewed_by": "FICTIONAL TEST reviewer",
                        "flags": []},
        }],
    }


def reasons(result):
    return " ".join(result["reasons"] + [
        reason for item in result["securities"] for reason in item["reasons"]])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fictional_bundle()

    def check_pas(self, bundle=None, expected=None):
        result = evaluate(self.bundle if bundle is None else bundle, NOW)
        self.assertEqual(result["decision"], "PAS")
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(result["candidates"], [])
        if expected:
            self.assertIn(expected, reasons(result))
        json.dumps(result, allow_nan=False)
        return result

    def draft(self):
        result = evaluate(self.bundle, NOW)
        self.assertEqual(result["decision"], "HAZIRLIK", reasons(result))
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(len(result["candidates"]), 1)
        json.dumps(result, allow_nan=False)
        return result["candidates"][0]

    def test_complete_fictional_data_produces_exact_nonexecuting_draft(self):
        draft = self.draft()
        self.assertFalse(draft["execution_enabled"])
        self.assertEqual(draft["quantity"], 156)
        self.assertEqual(draft["entry_debit_try"], "12482.00")
        self.assertEqual(draft["planned_stop_usd"], "1.94")
        self.assertEqual(draft["estimated_loss_try"], "439.87")
        self.assertEqual(draft["remaining_cash_try"], "37518.00")

    def test_every_top_level_required_input_is_fail_closed(self):
        for key in fictional_bundle():
            with self.subTest(key=key):
                bundle = fictional_bundle()
                del bundle[key]
                self.check_pas(bundle)

    def test_financial_numbers_reject_nan_infinity_boolean_and_structures(self):
        for value in (float("nan"), float("inf"), "NaN", "-Infinity", True,
                      False, None, [], {}, "abc", "1e999999", "1e-999999"):
            with self.subTest(value=value):
                bundle = fictional_bundle()
                bundle["fx"]["usdtry_ask"] = value
                self.check_pas(bundle)

    def test_public_api_handles_arbitrary_json_shapes_without_exception(self):
        malformed = (None, [], True, 1, "bad", {}, {"securities": [None, [], 1]},
                     {"mode": [], "errors": {}, "quote_source": True})
        for value in malformed:
            with self.subTest(value=value):
                result = evaluate(value, NOW)
                self.assertEqual(result["decision"], "PAS")
                self.assertEqual(result["candidates"], [])
        for section in ("market", "fx", "account", "costs", "quote_source"):
            for value in (None, [], True, 1, "bad"):
                bundle = fictional_bundle()
                bundle[section] = value
                self.check_pas(bundle)

    def test_nested_required_values_missing_or_malformed_never_ready(self):
        for section in ("quote", "trade", "metrics", "news", "filings"):
            reference = fictional_bundle()["securities"][0][section]
            for key in reference:
                for value in (None, [], {}, True):
                    # The true boolean is the valid value of material only.
                    if section == "news" and key == "material" and value is True:
                        continue
                    with self.subTest(section=section, key=key, value=value):
                        bundle = fictional_bundle()
                        bundle["securities"][0][section][key] = value
                        # An empty flags list is an explicit, valid clear review.
                        if section == "filings" and key == "flags" and value == []:
                            continue
                        self.check_pas(bundle)

    def test_non_sip_or_delayed_or_unrecognized_provider_blocked(self):
        for key, value in (("feed", "iex"), ("feed", "delayed_sip"),
                           ("provider", "unknown"), ("realtime", False),
                           ("realtime", "true")):
            bundle = fictional_bundle()
            bundle["quote_source"][key] = value
            self.check_pas(bundle, "REALTIME_ALPACA_SIP_REQUIRED")

    def test_prices_include_one_and_five_dollars_only(self):
        for price in ("1", "5"):
            self.bundle = fictional_bundle()
            item = self.bundle["securities"][0]
            item["quote"].update(bid=price, ask=price)
            item["trade"]["price"] = price
            item["metrics"]["vwap"] = str(Decimal(price) - Decimal("0.10"))
            self.draft()
        for section, key in (("quote", "ask"), ("trade", "price")):
            for price in ("0.9999", "5.0001"):
                bundle = fictional_bundle()
                bundle["securities"][0][section][key] = price
                self.check_pas(bundle)

    def test_both_spread_limits_inclusive_and_independently_enforced(self):
        item = self.bundle["securities"][0]
        item["quote"].update(bid="2", ask="2.05")
        self.draft()  # Exactly $0.05 AND 2.5% of bid.
        for bid, ask in (("4", "4.050001"), ("1", "1.025001")):
            bundle = fictional_bundle()
            bundle["securities"][0]["quote"].update(bid=bid, ask=ask)
            self.check_pas(bundle, "SPREAD_LIMIT_EXCEEDED")

    def test_crossed_zero_and_negative_quote_markets_are_rejected(self):
        for bid, ask in (("2.01", "2"), ("0", "2"), ("-1", "2"), ("1", "0")):
            bundle = fictional_bundle()
            bundle["securities"][0]["quote"].update(bid=bid, ask=ask)
            self.check_pas(bundle)

    def test_quote_sizes_must_be_positive_whole_shares(self):
        for key in ("bid_size", "ask_size"):
            for value in (-1, 0, "0.1", False):
                bundle = fictional_bundle()
                bundle["securities"][0]["quote"][key] = value
                self.check_pas(bundle)

    def test_displayed_liquidity_below_full_draft_is_rejected(self):
        self.bundle["securities"][0]["quote"]["ask_size"] = 155
        self.check_pas(expected="INSUFFICIENT_DISPLAYED_ASK_LIQUIDITY")
        self.bundle["securities"][0]["quote"]["ask_size"] = 156
        self.assertEqual(self.draft()["quantity"], 156)

    def test_stop_rounds_up_so_price_distance_never_exceeds_three_percent(self):
        item = self.bundle["securities"][0]
        item["quote"].update(bid="1.02", ask="1.03")
        item["trade"]["price"] = "1.03"
        item["metrics"]["vwap"] = "1"
        self.bundle["costs"]["sub_dollar_exit_covered"] = False
        draft = self.draft()
        self.assertEqual(draft["planned_stop_usd"], "1.00")
        self.assertLessEqual(Decimal(draft["actual_stop_distance_pct"]), Decimal("3"))

    def test_sub_dollar_exit_requires_explicit_applicable_fee_coverage(self):
        item = self.bundle["securities"][0]
        item["quote"].update(bid="1", ask="1")
        item["trade"]["price"] = "1"
        item["metrics"]["vwap"] = "0.95"
        self.bundle["costs"]["sub_dollar_exit_covered"] = False
        self.check_pas(expected="SUB_DOLLAR_EXIT_COST_NOT_VERIFIED")
        self.bundle["costs"]["sub_dollar_exit_covered"] = True
        self.assertEqual(self.draft()["planned_stop_usd"], "0.97")

    def test_daily_realized_plus_unrealized_loss_at_limit_halts(self):
        self.bundle["account"].update(realized_pnl_try="-2000", unrealized_pnl_try="-500")
        self.check_pas(expected="DAILY_LOSS_LIMIT_REACHED")
        self.bundle["account"].update(realized_pnl_try="500", unrealized_pnl_try="-1000")
        self.assertEqual(self.draft()["daily_loss_try"], "500.00")

    def test_daily_pnl_requires_current_session_basis_after_fees(self):
        for scope in (None, "lifetime", "since_entry", "current_session_gross", True):
            bundle = fictional_bundle()
            bundle["account"]["pnl_scope"] = scope
            self.check_pas(bundle, "CURRENT_SESSION_NET_FEES_REQUIRED")
        del self.bundle["account"]["pnl_scope"]
        self.check_pas(expected="CURRENT_SESSION_NET_FEES_REQUIRED")

    def test_combined_day_loss_existing_risk_costs_and_new_risk_limit_quantity(self):
        self.bundle["account"].update(realized_pnl_try="-2000", open_risk_try="400")
        draft = self.draft()
        self.assertEqual(draft["quantity"], 34)
        self.assertLessEqual(Decimal(draft["total_day_risk_try"]), Decimal("2500"))
        self.assertGreater(Decimal(draft["estimated_loss_try"]), Decimal("34") * Decimal("2.4"))
        self.bundle["account"]["open_risk_try"] = "500"
        self.check_pas(expected="DAILY_RISK_BUDGET_EXHAUSTED")

    def test_fx_bid_exit_conversion_and_fees_are_in_risk(self):
        draft = self.draft()
        expected = (Decimal("156") * (Decimal("2") * Decimal("40") -
                    Decimal("1.94") * Decimal("39.9") + Decimal("80") * Decimal("0.0025"))
                    + Decimal("4"))
        self.assertLess(Decimal(draft["estimated_loss_try"]) - expected, Decimal("0.01"))
        self.assertGreaterEqual(Decimal(draft["estimated_loss_try"]), expected)
        self.bundle["fx"]["usdtry_bid"] = "40.01"
        self.check_pas(expected="CROSSED_FX_MARKET")

    def test_existing_same_symbol_position_and_entry_fee_reduce_position_capacity(self):
        self.bundle["account"]["positions"] = [{"symbol": "FICT", "market_value_try": "12000"}]
        draft = self.draft()
        self.assertEqual(draft["quantity"], 6)
        self.assertEqual(draft["post_trade_symbol_commitment_try"], "12482.00")
        self.bundle["account"]["positions"] = [{"symbol": "FICT", "market_value_try": "12500"}]
        self.check_pas(expected="NO_REMAINING_CASH_POSITION_CAPITAL_OR_RISK_BUDGET")

    def test_account_position_lots_are_aggregated(self):
        self.bundle["account"]["positions"] = [
            {"symbol": "FICT", "market_value_try": "6000"},
            {"symbol": "FICT", "market_value_try": "6000"}]
        self.assertEqual(self.draft()["quantity"], 6)
        self.bundle["account"]["positions"][1]["market_value_try"] = "6501"
        self.check_pas(expected="EXISTING_POSITION_CAP_EXCEEDED")

    def test_cash_and_total_capital_are_enforced(self):
        self.bundle["account"].update(cash_try="1000", reserved_try="0")
        self.assertEqual(self.draft()["quantity"], 12)
        self.bundle["account"].update(cash_try="50000", reserved_try="0")
        self.bundle["account"]["positions"] = [
            {"symbol": symbol, "market_value_try": "12500"} for symbol in ("FICTA", "FICTB", "FICTC")]
        self.assertEqual(self.draft()["quantity"], 156)
        self.bundle["account"]["positions"].append({"symbol": "FICTD", "market_value_try": "12500"})
        self.check_pas()

    def test_unattributed_pending_order_reservations_block_new_drafts(self):
        for reserve in ("0.01", "100", "12500"):
            bundle = fictional_bundle()
            bundle["account"]["reserved_try"] = reserve
            self.check_pas(bundle, "PENDING_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION")

    def test_unknown_fees_restrictions_or_settlement_block_analysis(self):
        for section, key in (("costs", "entry_fee_try"), ("costs", "exit_fee_try"),
                             ("costs", "source"), ("costs", "verified_at"),
                             ("account", "trading_allowed"), ("account", "restrictions_verified_at")):
            bundle = fictional_bundle()
            del bundle[section][key]
            self.check_pas(bundle)
        self.bundle["costs"].update(entry_fee_try="0", exit_fee_try="0")
        self.draft()  # Explicit verified zero differs from absent fees.

    def test_negative_account_values_except_pnl_and_negative_costs_are_invalid(self):
        for section, key in (("account", "cash_try"), ("account", "open_risk_try"),
                             ("account", "reserved_try"), ("costs", "entry_fee_try"),
                             ("costs", "exit_fee_try"), ("costs", "slippage_pct")):
            bundle = fictional_bundle()
            bundle[section][key] = "-1"
            self.check_pas(bundle)

    def test_freshness_boundaries_for_quotes_trades_metrics_and_context(self):
        for path, maximum in ((('securities', 0, 'quote'), 15),
                              (('securities', 0, 'trade'), 30),
                              (('securities', 0, 'metrics'), 60),
                              (('market',), 60), (('account',), 300), (('fx',), 300)):
            with self.subTest(path=path):
                self.bundle = fictional_bundle()
                target = self.bundle
                for key in path:
                    target = target[key]
                target['as_of'] = stamp(maximum)
                self.draft()
                target['as_of'] = stamp(maximum + 0.001)
                self.check_pas(expected="STALE")

    def test_complete_five_minute_window_has_separate_freshness(self):
        metrics = self.bundle["securities"][0]["metrics"]
        metrics["window_end"] = stamp(330)
        self.draft()
        metrics["window_end"] = stamp(331)
        self.check_pas(expected="window_end:STALE")
        del metrics["window_end"]
        self.check_pas(expected="window_end:MISSING_OR_INVALID_AWARE_TIMESTAMP")

    def test_future_tolerance_is_five_seconds_and_timezone_is_mandatory(self):
        quote = self.bundle["securities"][0]["quote"]
        quote["as_of"] = stamp(-5)
        self.draft()
        quote["as_of"] = stamp(-5.001)
        self.check_pas(expected="FUTURE_TIMESTAMP")
        quote["as_of"] = "2026-10-08T15:00:00"
        self.check_pas(expected="AWARE_TIMESTAMP")
        result = evaluate(fictional_bundle(), NOW.replace(tzinfo=None))
        self.assertEqual(result["decision"], "PAS")

    def test_market_and_account_dates_use_new_york_session(self):
        for section in ("account", "market"):
            bundle = fictional_bundle()
            bundle[section]["session_date"] = "2026-10-07"
            self.check_pas(bundle, "NOT_CURRENT_NEW_YORK_DATE")
        self.bundle["market"]["is_open"] = False
        self.check_pas(expected="REGULAR_SESSION_NOT_VERIFIED_OPEN")

    def test_weak_volume_momentum_or_vwap_rejects_security(self):
        for key, value in (("day_volume", 999999), ("day_dollar_volume", "1999999.99"),
                           ("rvol_5m", "1.999"), ("return_5m_pct", "0.999"),
                           ("day_change_pct", "2.999"), ("vwap", "1.99")):
            bundle = fictional_bundle()
            bundle["securities"][0]["metrics"][key] = value
            self.check_pas(bundle)

    def test_catalyst_must_be_material_verified_recent_and_reviewed(self):
        for key, value in (("status", "unknown"), ("material", False),
                           ("published_at", stamp(86401)), ("reviewed_at", stamp(1300)),
                           ("category", "rumor"), ("url", "http://example.test/news")):
            bundle = fictional_bundle()
            bundle["securities"][0]["news"][key] = value
            self.check_pas(bundle)

    def test_financing_review_unknown_stale_incomplete_or_flagged_fails(self):
        for key, value in (("status", "unknown"), ("status", "risk"),
                           ("flags", ["ATM"]), ("flags", ["dilution"]),
                           ("flags", ["convertible financing"]), ("checked_at", stamp(86401)),
                           ("coverage_days", 364), ("reviewed_by", "")):
            bundle = fictional_bundle()
            bundle["securities"][0]["filings"][key] = value
            self.check_pas(bundle)

    def test_only_one_best_draft_is_returned(self):
        other = deepcopy(self.bundle["securities"][0])
        other["symbol"] = "FICTB"
        other["metrics"]["rvol_5m"] = "4"
        self.bundle["securities"].append(other)
        self.assertEqual(self.draft()["symbol"], "FICTB")

    def test_one_bad_ticker_does_not_hide_an_independent_valid_one(self):
        other = deepcopy(self.bundle["securities"][0])
        other["symbol"] = "FICTB"
        other["filings"]["flags"] = ["ATM"]
        self.bundle["securities"].append(other)
        self.assertEqual(self.draft()["symbol"], "FICT")

    def test_duplicate_security_or_upstream_error_blocks_output(self):
        self.bundle["securities"].append(deepcopy(self.bundle["securities"][0]))
        self.check_pas(expected="DUPLICATE_SECURITY")
        self.bundle = fictional_bundle()
        self.bundle["errors"] = ["provider HTTP 403"]
        self.check_pas(expected="UPSTREAM_ERROR:provider HTTP 403")

    def test_simulation_and_simulated_flag_always_pas_without_candidates(self):
        self.bundle["mode"] = "simulation"
        self.assertTrue(self.check_pas(expected="SIMULATION_INPUT_NOT_TRADABLE")["simulated"])
        self.bundle["mode"] = "live"
        self.bundle["simulated"] = True
        self.assertTrue(self.check_pas()["simulated"])
        self.bundle["simulated"] = "true"
        self.check_pas(expected="BOOLEAN_REQUIRED")

    def test_inputs_cannot_override_hard_limits_or_execution_authority(self):
        self.bundle.update(execution_enabled=True, capital_try=5000000, stop_pct=99,
                           daily_loss_limit_try=5000000, max_position_try=5000000)
        draft = self.draft()
        self.assertEqual(draft["quantity"], 156)
        self.assertEqual(draft["planned_stop_pct"], "3")
        self.bundle["account"]["realized_pnl_try"] = "-2500"
        self.check_pas()

    def test_evaluation_does_not_mutate_input(self):
        original = deepcopy(self.bundle)
        self.draft()
        self.assertEqual(self.bundle, original)


if __name__ == "__main__":
    unittest.main()
