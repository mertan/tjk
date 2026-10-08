"""Entirely synthetic provider -> risk engine tests; no credentials or network.

TESTX, prices, news, SEC rows, account figures and FX below are fictional. The
fixture's live mode exercises validation gates; it is never a market signal.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import unittest

from equity_guard.engine import evaluate
from equity_guard.providers import DATA, SEC, ReadOnlyProvider, _iso
from test_providers import ENV, NOW, fixtures


class ScannerPipelineTests(unittest.TestCase):
    def setUp(self):
        self.context, self.payloads, self.calls, self.getter = fixtures()
        stamp = _iso(NOW)
        self.context.update(
            fx={"usdtry_ask": "40", "usdtry_bid": "39.9", "as_of": stamp,
                "source": "FICTIONAL executable FX"},
            account={"as_of": stamp, "restrictions_verified_at": stamp,
                     "session_date": "2026-10-08", "source": "FICTIONAL account",
                     "pnl_scope": "current_session_net_fees", "cash_try": "50000",
                     "realized_pnl_try": "0", "unrealized_pnl_try": "0",
                     "open_risk_try": "0", "reserved_try": "0", "positions": [],
                     "trading_allowed": True},
            costs={"source": "FICTIONAL verified fees", "verified_at": stamp,
                   "entry_fee_try": "2", "exit_fee_try": "2", "slippage_pct": "0.25",
                   "sub_dollar_exit_covered": True},
        )
        self.context["symbols"][0]["filings_review"]["checked_at"] = _iso(NOW - timedelta(hours=1))
        for bar in self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]:
            bar["v"] *= 200

    def scan(self, environ=None):
        evidence = ReadOnlyProvider(getter=self.getter, environ=ENV if environ is None else environ).collect(self.context, NOW)
        result = evaluate(evidence, NOW)
        self.assertIs(result["execution_enabled"], False)
        return evidence, result

    def assert_pas(self, expected, environ=None):
        evidence, result = self.scan(environ)
        self.assertEqual(result["decision"], "PAS")
        self.assertEqual(result["candidates"], [])
        reasons = result["reasons"] + [
            reason for security in result["securities"] for reason in security["reasons"]]
        self.assertIn(expected, " ".join(reasons))
        return evidence, result

    def test_complete_synthetic_pipeline_produces_only_bounded_draft(self):
        before = deepcopy(self.context)
        evidence, result = self.scan()
        self.assertEqual(evidence["errors"], [])
        self.assertEqual(result["decision"], "HAZIRLIK", result)
        self.assertEqual(len(result["candidates"]), 1)
        draft = result["candidates"][0]
        self.assertIs(draft["execution_enabled"], False)
        # A normal quote and passing risk budget cannot certify halt/broker state.
        self.assertIn("CURRENT_HALT_LULD_STATUS", draft["manual_review_required"])
        self.assertLessEqual(Decimal(draft["entry_debit_try"]), Decimal("12500"))
        self.assertLessEqual(Decimal(draft["total_day_risk_try"]), Decimal("2500"))
        self.assertLessEqual(Decimal(draft["actual_stop_distance_pct"]), Decimal("3"))
        self.assertEqual(draft["planned_stop_pct"], "3")
        self.assertEqual(self.context, before)

    def test_missing_credentials_never_calls_transport_or_fabricates_price(self):
        evidence, _ = self.assert_pas("alpaca_credentials_missing", environ={})
        self.assertEqual(self.calls, [])
        self.assertEqual(evidence["securities"], [])
        self.assertIs(evidence["quote_source"]["realtime"], False)

    def test_sip_entitlement_is_required_before_any_transport_call(self):
        env = {key: value for key, value in ENV.items() if key != "ALPACA_SIP_CONFIRMED"}
        self.assert_pas("real_time_sip_entitlement_unconfirmed", environ=env)
        self.assertEqual(self.calls, [])

    def test_actual_provider_spread_and_stale_quote_flow_to_pas(self):
        quote = self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestQuote"]
        quote.update(bp=2.0, ap=2.06)
        self.assert_pas("SPREAD_LIMIT_EXCEEDED")
        quote.update(bp=2.05, ap=2.06, t=_iso(NOW - timedelta(seconds=16)))
        self.assert_pas("quote_or_trade_stale")

    def test_nonfirm_closed_or_unknown_quotes_cannot_become_drafts(self):
        quote = self.payloads[DATA + "/v2/stocks/snapshots"]["TESTX"]["latestQuote"]
        for conditions in (["N"], ["U"], ["L"], ["Z"], ["R", "N"], ["UNKNOWN"], []):
            with self.subTest(conditions=conditions):
                quote["c"] = conditions
                self.assert_pas("regular_quote_condition_unverified")

    def test_weak_volume_and_momentum_fail_from_actual_calculated_metrics(self):
        bars = self.payloads[DATA + "/v2/stocks/bars"]["bars"]["TESTX"]
        for bar in bars:
            bar["v"] //= 200
        self.assert_pas("DAY_VOLUME_BELOW_DESIGN_THRESHOLD")
        for bar in bars:
            bar["v"] *= 200
        bars[-1]["c"] = bars[-1]["o"]
        self.assert_pas("MOMENTUM_5M_BELOW_DESIGN_THRESHOLD")

    def test_headline_without_material_review_never_becomes_candidate(self):
        self.context["symbols"][0]["news_review"] = {}
        evidence, _ = self.assert_pas("UNVERIFIED_MATERIAL_CATALYST")
        self.assertEqual(evidence["securities"][0]["news"]["status"], "unknown")
        self.assertEqual(len(evidence["securities"][0]["news"]["review_candidates"]), 1)

    def test_automatic_shelf_registration_blocks_even_manual_clear(self):
        recent = self.payloads[SEC + "/submissions/CIK0000001234.json"]["filings"]["recent"]
        recent["form"][0] = "S-3ASR"
        evidence, _ = self.assert_pas("FINANCING_DILUTION_REVIEW_NOT_CLEAR")
        self.assertEqual(evidence["securities"][0]["filings"]["status"], "risk")

    def test_daily_realized_plus_unrealized_loss_threshold_blocks(self):
        self.context["account"].update(realized_pnl_try="-2000", unrealized_pnl_try="-500")
        self.assert_pas("DAILY_LOSS_LIMIT_REACHED")

    def test_existing_symbol_and_capital_commitment_are_respected(self):
        account = self.context["account"]
        account.update(cash_try="100", open_risk_try="100",
                       positions=[{"symbol": "TESTX", "market_value_try": "12400"},
                                  {"symbol": "OTHER", "market_value_try": "12500"},
                                  {"symbol": "THIRD", "market_value_try": "12500"},
                                  {"symbol": "FOURTH", "market_value_try": "12500"}])
        _, result = self.scan()
        self.assertEqual(result["decision"], "HAZIRLIK", result)
        draft = result["candidates"][0]
        self.assertEqual(draft["quantity"], 1)
        self.assertLessEqual(Decimal(draft["post_trade_symbol_commitment_try"]), Decimal("12500"))
        self.assertLessEqual(Decimal(draft["entry_debit_try"]) + Decimal("49900"), Decimal("50000"))
        account["positions"][0]["market_value_try"] = "12500"
        self.assert_pas("NO_REMAINING_CASH_POSITION_CAPITAL_OR_RISK_BUDGET")


if __name__ == "__main__":
    unittest.main()
