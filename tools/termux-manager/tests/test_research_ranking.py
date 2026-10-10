"""Fictional local research exports. No market-data or network fixtures."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from equity_guard.research_ranking import select


NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)


def row(symbol="TESTX", *, price="3", previous="2.5", volume=100000, age=0):
    return {"symbol": symbol, "exchange": "NASDAQ", "price_usd": price,
            "previous_close_usd": previous, "volume_shares": volume,
            "as_of": (NOW - timedelta(seconds=age)).isoformat(),
            "session_date": "2026-10-08"}


def fixture(records=None):
    return {"schema_version": 1, "source": {
        "provider": "licensed_public_export", "url": "https://licensed.example/export",
        "terms_url": "https://licensed.example/terms", "permission": "personal_automated_analysis",
        "permission_reviewed_at": NOW.isoformat(),
        "verification": "operator_reviewed_original_export", "retrieved_at": NOW.isoformat(),
        "delay_seconds": 0, "volume_scope": "consolidated_us",
        "volume_basis": "session_cumulative_shares"}, "records": [row()] if records is None else records}


def listed(data):
    return {item["symbol"]: {"exchange": item["exchange"]} for item in data["records"]}


class ResearchRankingTests(unittest.TestCase):
    def run_selection(self, data, *, listing=None, now=NOW):
        return select(data, listed(data) if listing is None else listing, now)

    def assert_unavailable(self, data, error=None):
        result = self.run_selection(data)
        self.assertEqual("unavailable", result["status"])
        self.assertEqual([], result["selected_symbols"])
        self.assertEqual([], result["research_priority"])
        if error is not None:
            self.assertIn(error, result["errors"])
        return result

    def test_missing_input_never_falls_back_to_alphabetical(self):
        result = select(None, {"AAA": {"exchange": "NASDAQ"}}, NOW)
        self.assertEqual(["RANKING_INPUT_REQUIRED"], result["errors"])
        self.assertEqual([], result["selected_symbols"])

    def test_basic_export_is_research_only_and_not_a_recommendation(self):
        result = self.run_selection(fixture())
        self.assertEqual("available", result["status"])
        self.assertEqual(["TESTX"], result["selected_symbols"])
        entry = result["research_priority"][0]
        self.assertEqual("PAS", entry["decision"])
        self.assertFalse(entry["execution_enabled"])
        self.assertFalse(entry["executable_nbbo"])
        self.assertEqual("RESEARCH_COLLECTION_PRIORITY_ONLY", entry["purpose"])
        self.assertEqual("OPERATOR_ATTESTATION_NOT_INDEPENDENTLY_VERIFIED", result["source"]["assurance"])
        self.assertEqual("SUPPLIED_EXPORT_ONLY", result["source"]["ranking_scope"])
        self.assertNotIn("shares", entry)
        json.dumps(result, allow_nan=False)

    def test_strong_z_symbol_outside_alphabetical_twenty_is_selected_first(self):
        rows = [row(f"A{i:02d}", price="2.1", previous="2", volume=1000 + i) for i in range(25)]
        rows.append(row("ZZTOP", price="4", previous="2", volume=10000000))
        result = self.run_selection(fixture(rows))
        self.assertEqual("ZZTOP", result["selected_symbols"][0])
        self.assertEqual(20, len(result["selected_symbols"]))
        self.assertEqual(26, result["input_count"])
        self.assertEqual(26, result["eligible_count"])

    def test_volume_changes_priority_when_price_movement_is_equal(self):
        result = self.run_selection(fixture([row("AAA", volume=100), row("ZZZ", volume=200)]))
        self.assertEqual(["ZZZ", "AAA"], result["selected_symbols"])

    def test_price_movement_changes_priority_when_volume_is_equal(self):
        result = self.run_selection(fixture([row("AAA", price="3"), row("ZZZ", price="4")]))
        self.assertEqual(["ZZZ", "AAA"], result["selected_symbols"])

    def test_balanced_borda_score_uses_both_factors_equally(self):
        data = fixture([row("VOLUME", price="2.2", previous="2", volume=9000),
                        row("BALANCE", price="3", previous="2", volume=3000),
                        row("MOVE", price="4", previous="2", volume=1000)])
        result = self.run_selection(data)
        self.assertEqual(["MOVE", "BALANCE", "VOLUME"], result["selected_symbols"])
        self.assertEqual(["50", "50", "50"], [item["rank_score"] for item in result["research_priority"]])
        self.assertEqual("100", result["research_priority"][0]["change_percentile"])
        self.assertEqual("0", result["research_priority"][0]["volume_percentile"])

    def test_equal_factors_tie_by_symbol_and_input_order_does_not_matter(self):
        data = fixture([row("ZZZ"), row("BBB"), row("AAA")])
        result = self.run_selection(data)
        data["records"].reverse()
        self.assertEqual(result, self.run_selection(data))
        self.assertEqual(["AAA", "BBB", "ZZZ"], result["selected_symbols"])

    def test_delayed_observation_reports_age_delay_and_pas(self):
        data = fixture([row(age=900)])
        data["source"]["delay_seconds"] = 900
        result = self.run_selection(data)
        self.assertEqual("available", result["status"])
        entry = result["research_priority"][0]
        self.assertEqual(900, entry["data_age_seconds"])
        self.assertEqual(900, entry["declared_delay_seconds"])
        self.assertTrue(entry["delayed"])
        self.assertEqual("PAS", entry["decision"])
        self.assertTrue(result["source"]["declared_delayed"])

    def test_iex_is_reported_as_iex_and_never_consolidated(self):
        data = fixture()
        data["source"]["volume_scope"] = "iex"
        result = self.run_selection(data)
        self.assertEqual("iex", result["source"]["volume_scope"])
        self.assertEqual("iex", result["research_priority"][0]["volume_scope"])
        self.assertFalse(result["research_priority"][0]["executable_nbbo"])

    def test_aged_zero_delay_export_is_still_marked_delayed(self):
        data = fixture([row(age=900)])
        result = self.run_selection(data)
        entry = result["research_priority"][0]
        self.assertEqual(0, entry["declared_delay_seconds"])
        self.assertTrue(entry["delayed"])
        self.assertTrue(entry["age_exceeds_live_trade_limit"])
        self.assertEqual("PAS", entry["decision"])

    def test_row_stale_one_second_beyond_bound_is_rejected(self):
        result = self.assert_unavailable(fixture([row(age=901)]), "RANKING_NO_ELIGIBLE_OBSERVATIONS")
        self.assertEqual(["STALE_TIMESTAMP"], result["rejected"][0]["errors"])

    def test_row_future_one_microsecond_is_rejected(self):
        data = fixture()
        data["records"][0]["as_of"] = (NOW + timedelta(microseconds=1)).isoformat()
        result = self.assert_unavailable(data)
        self.assertEqual(["FUTURE_TIMESTAMP"], result["rejected"][0]["errors"])

    def test_one_stale_row_does_not_poison_valid_rows(self):
        result = self.run_selection(fixture([row("AAA", age=901), row("ZZZ")]))
        self.assertEqual(["ZZZ"], result["selected_symbols"])
        self.assertEqual("AAA", result["rejected"][0]["symbol"])

    def test_row_timezone_required(self):
        data = fixture()
        data["records"][0]["as_of"] = "2026-10-08T14:00:00"
        result = self.assert_unavailable(data)
        self.assertIn("INVALID_AWARE_TIMESTAMP", result["rejected"][0]["errors"])

    def test_timestamp_timezone_offset_is_respected(self):
        data = fixture()
        data["records"][0]["as_of"] = "2026-10-08T10:00:00-04:00"
        self.assertEqual("available", self.run_selection(data)["status"])

    def test_previous_session_is_rejected_even_if_timestamp_is_fresh(self):
        data = fixture()
        data["records"][0]["session_date"] = "2026-10-07"
        result = self.assert_unavailable(data)
        self.assertIn("NOT_CURRENT_NEW_YORK_SESSION", result["rejected"][0]["errors"])

    def test_timestamp_on_previous_new_york_date_is_rejected(self):
        now = datetime(2026, 10, 8, 4, 1, tzinfo=timezone.utc)
        data = fixture()
        data["source"]["retrieved_at"] = now.isoformat()
        data["source"]["permission_reviewed_at"] = now.isoformat()
        data["records"][0]["as_of"] = "2026-10-08T03:59:00+00:00"
        result = self.run_selection(data, now=now)
        self.assertEqual(["NOT_CURRENT_NEW_YORK_SESSION"], result["rejected"][0]["errors"])

    def test_invalid_session_dates_are_rejected(self):
        for day in (None, [], True, "2026-99-99", "20261008"):
            data = fixture()
            data["records"][0]["session_date"] = day
            with self.subTest(day=day):
                result = self.assert_unavailable(data)
                self.assertEqual(["INVALID_SESSION_DATE"], result["rejected"][0]["errors"])

    def test_snapshot_skew_at_boundary_is_accepted(self):
        result = self.run_selection(fixture([row("AAA", age=60), row("ZZZ", age=0)]))
        self.assertEqual("available", result["status"])

    def test_incomparable_snapshot_skew_rejects_entire_ranking(self):
        self.assert_unavailable(fixture([row("AAA", age=61), row("ZZZ", age=0)]),
                                "RANKING_SNAPSHOT_INCOMPARABLE")

    def test_observation_cannot_postdate_export_retrieval(self):
        data = fixture()
        data["source"]["retrieved_at"] = (NOW - timedelta(seconds=1)).isoformat()
        result = self.assert_unavailable(data)
        self.assertEqual(["DATA_AFTER_RETRIEVAL"], result["rejected"][0]["errors"])

    def test_retrieval_older_than_five_minutes_rejects_export(self):
        data = fixture([row(age=301)])
        data["source"]["retrieved_at"] = (NOW - timedelta(seconds=301)).isoformat()
        self.assert_unavailable(data, "RANKING_RETRIEVAL_STALE_TIMESTAMP")

    def test_retrieval_at_exact_five_minute_boundary_is_accepted(self):
        data = fixture([row(age=300)])
        data["source"]["retrieved_at"] = (NOW - timedelta(seconds=300)).isoformat()
        self.assertEqual("available", self.run_selection(data)["status"])

    def test_unknown_negative_excessive_or_boolean_delay_is_rejected(self):
        for delay in (None, -1, 901, True, "0", 0.0):
            data = fixture()
            data["source"]["delay_seconds"] = delay
            with self.subTest(delay=delay):
                self.assert_unavailable(data, "RANKING_DELAY_UNKNOWN_OR_UNSUPPORTED")

    def test_permission_attestation_is_mandatory(self):
        for field in ("permission", "verification"):
            data = fixture()
            data["source"][field] = "publicly_accessible"
            self.assert_unavailable(data, "RANKING_PERMISSION_ATTESTATION_REQUIRED")

    def test_permission_review_expires_after_thirty_days(self):
        data = fixture()
        data["source"]["permission_reviewed_at"] = (NOW - timedelta(days=30, seconds=1)).isoformat()
        self.assert_unavailable(data, "RANKING_PERMISSION_STALE_TIMESTAMP")

    def test_future_retrieval_or_permission_review_is_rejected(self):
        for field, prefix in (("retrieved_at", "RETRIEVAL"), ("permission_reviewed_at", "PERMISSION")):
            data = fixture()
            data["source"][field] = (NOW + timedelta(seconds=1)).isoformat()
            self.assert_unavailable(data, "RANKING_" + prefix + "_FUTURE_TIMESTAMP")

    def test_source_provider_must_be_supported(self):
        data = fixture()
        data["source"]["provider"] = "free_scraping"
        self.assert_unavailable(data, "RANKING_PROVIDER_NOT_PERMITTED")

    def test_blocked_sources_cannot_masquerade_as_licensed_exports(self):
        for host in ("tradingview.com", "scanner.tradingview.com", "nasdaq.com",
                     "api.nasdaq.com", "stooq.com", "stooq.pl", "www.stooq.com"):
            for field in ("url", "terms_url"):
                data = fixture()
                data["source"][field] = "https://" + host + "/export"
                with self.subTest(host=host, field=field):
                    self.assert_unavailable(data, "RANKING_PROVIDER_NOT_PERMITTED")

    def test_generic_finviz_source_is_rejected(self):
        for host in ("finviz.com", "www.finviz.com", "elite.finviz.com"):
            data = fixture()
            data["source"]["url"] = "https://" + host + "/export"
            self.assert_unavailable(data, "RANKING_FINVIZ_ELITE_EXPORT_REQUIRED")

    def test_finviz_elite_export_attestation_is_supported_without_accessing_account(self):
        for host in ("finviz.com", "www.finviz.com", "elite.finviz.com"):
            data = fixture()
            data["source"].update(provider="finviz_elite_export", url="https://" + host + "/export",
                                  terms_url="https://finviz.com/terms.ashx")
            self.assertEqual("available", self.run_selection(data)["status"])

    def test_fake_finviz_hosts_are_rejected_for_elite_exports(self):
        for host in ("finviz.com.evil.example", "download.finviz.com", "example.com"):
            data = fixture()
            data["source"].update(provider="finviz_elite_export", url="https://" + host + "/export",
                                  terms_url="https://finviz.com/terms.ashx")
            self.assert_unavailable(data, "RANKING_FINVIZ_ELITE_EXPORT_REQUIRED")

    def test_unsafe_provenance_urls_are_rejected_without_echoing_them(self):
        for url in ("http://licensed.example/export", "https://user:secret@licensed.example/export",
                    "https://licensed.example/export?token=synthetic-secret", "https://licensed.example/#secret",
                    "https://licensed.example:444/export", "https://licensed.example/line\nbreak",
                    "https://licensed.example\\@evil.example/export", "https://licensed.example/export?",
                    "https://licensed.example/export#", "https://licensed.example./export"):
            data = fixture()
            data["source"]["url"] = url
            with self.subTest(url=url):
                result = self.assert_unavailable(data, "RANKING_SOURCE_URL_INVALID")
                self.assertIsNone(result["source"])
                self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_volume_basis_must_be_homogeneous_declared_session_volume(self):
        for field, value in (("volume_basis", "average_daily_volume"), ("volume_scope", "unknown")):
            data = fixture()
            data["source"][field] = value
            self.assert_unavailable(data, "RANKING_VOLUME_BASIS_UNSUPPORTED")

    def test_mixed_volume_row_override_is_schema_error(self):
        data = fixture()
        data["records"][0]["volume_scope"] = "iex"
        self.assert_unavailable(data, "RANKING_ROW_SCHEMA_INVALID")

    def test_duplicate_or_conflicting_symbol_rejects_all_rows(self):
        for second in (row(), row(price="4"), row(volume=900000)):
            self.assert_unavailable(fixture([row(), second]), "RANKING_DUPLICATE_OR_CONFLICTING_SYMBOL")

    def test_missing_or_extra_row_fields_invalidate_entire_input(self):
        for mutation in ("missing", "extra", "not_object"):
            data = fixture([row("AAA"), row("ZZZ")])
            if mutation == "missing":
                del data["records"][1]["volume_shares"]
            elif mutation == "extra":
                data["records"][1]["signal"] = "AL"
            else:
                data["records"][1] = None
            result = select(data, {"AAA": {"exchange": "NASDAQ"}, "ZZZ": {"exchange": "NASDAQ"}}, NOW)
            self.assertEqual(["RANKING_ROW_SCHEMA_INVALID"], result["errors"])
            self.assertEqual([], result["selected_symbols"])

    def test_top_schema_rejects_boolean_version_extra_or_missing_fields(self):
        for mutation in ("bool", "extra", "missing"):
            data = fixture()
            if mutation == "bool":
                data["schema_version"] = True
            elif mutation == "extra":
                data["max_symbols"] = 100
            else:
                del data["schema_version"]
            self.assert_unavailable(data, "RANKING_SCHEMA_INVALID")

    def test_source_extra_field_cannot_override_execution(self):
        data = fixture()
        data["source"]["execution_enabled"] = True
        self.assert_unavailable(data, "RANKING_SOURCE_SCHEMA_INVALID")

    def test_local_input_limit_is_ten_thousand_not_network_limit_twenty(self):
        rows = [row(f"T{i:05d}") for i in range(10000)]
        result = self.run_selection(fixture(rows))
        self.assertEqual(10000, result["eligible_count"])
        self.assertEqual(20, len(result["selected_symbols"]))
        rows.append(row("OVERLIMIT"))
        self.assert_unavailable(fixture(rows), "RANKING_RECORD_LIMIT_OR_SCHEMA_INVALID")

    def test_empty_records_are_unavailable(self):
        self.assert_unavailable(fixture([]), "RANKING_RECORD_LIMIT_OR_SCHEMA_INVALID")

    def test_unlisted_exchange_mismatch_and_amex_are_rejected(self):
        data = fixture([row("AAA"), row("BBB"), row("CCC"), row("DDD")])
        data["records"][2]["exchange"] = "AMEX"
        listing = {"BBB": {"exchange": "NYSE"}, "CCC": {"exchange": "AMEX"},
                   "DDD": {"exchange": "NASDAQ"}}
        result = self.run_selection(data, listing=listing)
        self.assertEqual(["DDD"], result["selected_symbols"])
        self.assertEqual(3, len(result["rejected"]))

    def test_nyse_is_supported(self):
        data = fixture()
        data["records"][0]["exchange"] = "NYSE"
        self.assertEqual("NYSE", self.run_selection(data)["research_priority"][0]["exchange"])

    def test_invalid_symbol_is_not_echoed(self):
        data = fixture()
        data["records"][0]["symbol"] = "sensitive malformed symbol"
        result = self.assert_unavailable(data)
        self.assertIsNone(result["rejected"][0]["symbol"])
        self.assertNotIn("sensitive malformed", json.dumps(result))

    def test_price_band_is_inclusive_and_imported_risk_boundaries_apply(self):
        data = fixture([row("ONE", price="1", previous="0.9"), row("FIVE", price="5", previous="4"),
                        row("BELOW", price="0.999999999999", previous="0.5"),
                        row("ABOVE", price="5.000000000001", previous="4")])
        result = self.run_selection(data)
        self.assertEqual({"ONE", "FIVE"}, set(result["selected_symbols"]))
        self.assertEqual(2, len(result["rejected"]))

    def test_positive_price_movement_and_volume_required(self):
        data = fixture([row("FLAT", price="2.5"), row("DOWN", price="2"), row("ZERO", volume=0), row("GOOD")])
        self.assertEqual(["GOOD"], self.run_selection(data)["selected_symbols"])

    def test_nonfinite_boolean_oversized_fractional_volume_rejected(self):
        cases = {"price_usd": [True, "NaN", "Infinity", "1e999999", "1e-999999", "0", -1],
                 "previous_close_usd": [False, "sNaN", "1e6.1", "1000001", None],
                 "volume_shares": [True, "1.1", "1000000000001", "Infinity", -1]}
        for field, values in cases.items():
            for value in values:
                data = fixture()
                data["records"][0][field] = value
                with self.subTest(field=field, value=value):
                    result = self.assert_unavailable(data)
                    self.assertEqual(["INVALID_PRICE_OR_VOLUME"], result["rejected"][0]["errors"])

    def test_high_precision_and_external_decimal_context_do_not_change_ranking(self):
        data = fixture([row("AAA", price="3.00000000000000000001"),
                        row("ZZZ", price="3.00000000000000000002")])
        expected = self.run_selection(data)
        with localcontext() as context:
            context.prec = 2
            result = self.run_selection(data)
        self.assertEqual(expected, result)
        self.assertEqual("ZZZ", result["selected_symbols"][0])

    def test_does_not_mutate_input(self):
        data = fixture()
        before = copy.deepcopy(data)
        self.run_selection(data)
        self.assertEqual(before, data)

    def test_now_must_be_timezone_aware(self):
        data = fixture()
        result = self.run_selection(data, now=NOW.replace(tzinfo=None))
        self.assertEqual(["RANKING_CONTEXT_INVALID"], result["errors"])


if __name__ == "__main__":
    unittest.main()
