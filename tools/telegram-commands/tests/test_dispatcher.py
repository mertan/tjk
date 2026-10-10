"""Synthetic fixtures only; no market, Telegram, source or account connections."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tjk_commands import (Candidate, CommandDispatcher, PredictionStore, PR6RaceProvider,
                          ProviderRegistration, SourceEvidence, StockRiskInputs)
from tjk_commands.gates import assess, stock_reasons
from tjk_commands.models import REQUIRED_EVIDENCE

NOW = datetime(2026, 10, 8, 18, tzinfo=timezone.utc)


def risk(**changes):
    return replace(StockRiskInputs(D("2"), D("2"), D("2.05"), D("40"), D("12500"),
                                  D("37500"), D("0"), D("2"), D("1"), True, True, True), **changes)


def candidate(sport="at", **changes):
    sources = tuple(SourceEvidence(name, "synthetic", "https://fixtures.example/data", NOW,
                                   True, 0) for name in REQUIRED_EVIDENCE[sport])
    return replace(Candidate("fixture-event", "winner", NOW + timedelta(minutes=10), "fixture-A",
                             sources, (), True, risk() if sport == "hisse" else None), **changes)


def update(command="/at", **changes):
    value = {"update_id": 7, "message": {"message_id": 8, "date": int(NOW.timestamp()),
             "chat": {"id": 100}, "from": {"id": 200, "is_bot": False}, "text": command}}
    value.update(changes)
    return value


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PredictionStore(Path(self.tmp.name) / "private" / "commands.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)

    def dispatcher(self, **kwargs):
        return CommandDispatcher(self.store, allowed_chat_ids={100}, allowed_user_ids={200},
                                 bot_username="fixture_bot", clock=lambda: NOW, **kwargs)

    def providers(self, callback=None, sport="at"):
        return {sport: ProviderRegistration("fixture-v1", callback or (lambda req: candidate(sport)),
                                             frozenset({"fixtures.example"}))}

    def test_all_sports_without_provider_persist_pas_not_pending(self):
        dispatcher = self.dispatcher()
        for i, sport in enumerate(("at", "basket", "futbol", "hisse")):
            reply = dispatcher.handle_update(update("/" + sport, update_id=i))
            self.assertIn("PAS", reply.text)
            self.assertIn("PROVIDER_UNAVAILABLE", reply.text)
        rows = self.store.stats("100")
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["pas"] == 1 and r["pending"] == 0 for r in rows))

    def test_fresh_complete_prediction_is_pending_and_reports_provenance(self):
        reply = self.dispatcher(providers=self.providers()).handle_update(update())
        self.assertIn("TAHMİN", reply.text)
        self.assertIn("fixture-v1", reply.text)
        self.assertIn("BEKLİYOR", reply.text)
        self.assertIn("gecikme: 0 sn", reply.text)
        self.assertIsNone(reply.parse_mode)
        self.assertEqual(self.store.stats("100")[0]["pending"], 1)

    def test_duplicate_update_does_not_duplicate_prediction(self):
        dispatcher = self.dispatcher(providers=self.providers())
        first = dispatcher.handle_update(update())
        second = dispatcher.handle_update(update())
        self.assertIn("TAHMİN", first.text)
        self.assertIn("PREVIOUS_RECORD_EXISTS", second.text)
        self.assertNotIn("fixture-A", second.text)
        self.assertEqual(self.store.stats("100")[0]["total"], 1)

    def test_negative_group_chat_ids_work_and_remain_scoped(self):
        dispatcher = CommandDispatcher(self.store, allowed_chat_ids={-100123}, allowed_user_ids={200},
                                       clock=lambda: NOW)
        item = update()
        item["message"]["chat"]["id"] = -100123
        self.assertIn("PROVIDER_UNAVAILABLE", dispatcher.handle_update(item).text)
        item["message"]["text"] = "/performans"
        self.assertIn("PAS 1", dispatcher.handle_update(item).text)
        self.assertEqual(self.store.stats("100"), [])

    def test_future_source_time_is_persisted_as_pas_with_safe_reason(self):
        value = candidate()
        value = replace(value, evidence=(replace(value.evidence[0], as_of=NOW + timedelta(seconds=1)),)
                        + value.evidence[1:])
        reply = self.dispatcher(providers=self.providers(lambda req: value)).handle_update(update())
        self.assertIn("SOURCE_TIME_IN_FUTURE", reply.text)
        self.assertNotIn("STORAGE_", reply.text)
        self.assertEqual(self.store.stats("100")[0]["pas"], 1)

    def test_retry_with_expired_data_never_releases_historical_choice(self):
        dispatcher = self.dispatcher(providers=self.providers())
        self.assertIn("TAHMİN", dispatcher.handle_update(update()).text)
        dispatcher.clock = lambda: NOW + timedelta(minutes=11)
        reply = dispatcher.handle_update(update())
        self.assertIn("PAS", reply.text)
        self.assertNotIn("fixture-A", reply.text)
        self.assertEqual(self.store.stats("100")[0]["total"], 1)

    def test_fresh_new_provider_event_on_same_update_cannot_release_closed_original(self):
        first = candidate(closes_at=NOW + timedelta(seconds=30))
        later = NOW + timedelta(seconds=60)
        second = candidate(event_id="fixture-B", evidence=tuple(
            replace(e, as_of=later) for e in candidate().evidence))
        callback = Mock(side_effect=(first, second))
        dispatcher = self.dispatcher(providers=self.providers(callback))
        self.assertIn("TAHMİN", dispatcher.handle_update(update()).text)
        dispatcher.clock = lambda: later
        reply = dispatcher.handle_update(update())
        self.assertIn("PREVIOUS_RECORD_EXISTS", reply.text)
        self.assertNotIn("fixture-A", reply.text)
        self.assertEqual(self.store.stats("100")[0]["total"], 1)

    def test_new_command_same_event_does_not_reissue_original_choice(self):
        dispatcher = self.dispatcher(providers=self.providers())
        dispatcher.handle_update(update())
        reply = dispatcher.handle_update(update(update_id=99))
        self.assertIn("PREVIOUS_RECORD_EXISTS", reply.text)
        self.assertNotIn("fixture-A", reply.text)
        self.assertEqual(self.store.stats("100")[0]["total"], 1)

    def test_authorization_precedes_provider_or_store(self):
        callback = Mock(side_effect=AssertionError("must not be called"))
        dispatcher = self.dispatcher(providers=self.providers(callback))
        for field, value in (("chat", {"id": 101}), ("from", {"id": 201, "is_bot": False}),
                             ("from", {"id": 200, "is_bot": True}), ("sender_chat", {"id": 100}),
                             ("forward_origin", {}), ("via_bot", {})):
            item = update()
            item["message"][field] = value
            self.assertIsNone(dispatcher.handle_update(item))
        callback.assert_not_called()
        self.assertEqual(self.store.stats("100"), [])

    def test_edited_channel_and_malformed_updates_ignored(self):
        dispatcher = self.dispatcher()
        for item in (None, [], {}, {"update_id": True, "message": {}},
                     {"update_id": 1, "edited_message": update()["message"]},
                     {"update_id": 1, "channel_post": update()["message"]}):
            self.assertIsNone(dispatcher.handle_update(item))

    def test_unknown_other_bot_or_noncommand_falls_through(self):
        dispatcher = self.dispatcher()
        for text in ("/unknown", "/at@other_bot", "merhaba /at", "/at\n/durum", "/At", "/atx"):
            self.assertIsNone(dispatcher.handle_update(update(text)))
        self.assertIn("PAS", dispatcher.handle_update(update("/at@FIXTURE_BOT")).text)

    def test_argument_limits_do_not_call_provider(self):
        callback = Mock()
        dispatcher = self.dispatcher(providers=self.providers(callback))
        self.assertIn("INVALID_COMMAND_ARGUMENTS", dispatcher.handle_update(update("/at " + "x" * 65)).text)
        self.assertIn("INVALID_COMMAND_ARGUMENTS", dispatcher.handle_update(update("/performans at")).text)
        callback.assert_not_called()

    def test_provider_receives_only_request_not_telegram_identity_or_tokens(self):
        callback = Mock(return_value=candidate())
        self.dispatcher(providers=self.providers(callback)).handle_update(update("/at fixture-event"))
        request = callback.call_args.args[0]
        self.assertEqual(request.sport, "at")
        self.assertEqual(request.args, ("fixture-event",))
        self.assertFalse(hasattr(request, "chat_id"))

    def test_old_future_and_missing_command_time_fail_closed_without_provider(self):
        callback = Mock()
        dispatcher = self.dispatcher(providers=self.providers(callback))
        for i, date in enumerate((int(NOW.timestamp()) - 301, int(NOW.timestamp()) + 31, None)):
            item = update(update_id=i)
            item["message"]["date"] = date
            self.assertIn("COMMAND_TIME_UNVERIFIED", dispatcher.handle_update(item).text)
        callback.assert_not_called()

    def test_provider_failure_redacts_exception_and_persists_pas(self):
        callback = Mock(side_effect=RuntimeError("synthetic-private-detail"))
        reply = self.dispatcher(providers=self.providers(callback)).handle_update(update())
        self.assertIn("PROVIDER_UNAVAILABLE", reply.text)
        self.assertNotIn("synthetic-private-detail", reply.text)
        self.assertEqual(self.store.stats("100")[0]["pas"], 1)

    def test_storage_failure_does_not_release_prediction_or_error_detail(self):
        with patch.object(self.store, "record_prediction", side_effect=RuntimeError("synthetic-private-detail")):
            reply = self.dispatcher(providers=self.providers()).handle_update(update())
        self.assertEqual(reply.text, "PAS | STORAGE_OR_PROVIDER_UNAVAILABLE")
        self.assertNotIn("fixture-A", reply.text)

    def test_malformed_provider_output_records_pas(self):
        reply = self.dispatcher(providers=self.providers(lambda req: {"selection": "forged"})).handle_update(update())
        self.assertIn("INVALID_PROVIDER_DATA", reply.text)
        self.assertEqual(self.store.stats("100")[0]["pas"], 1)

    def test_clock_rechecked_after_slow_provider(self):
        clock = Mock(side_effect=(NOW, NOW + timedelta(minutes=11)))
        dispatcher = self.dispatcher(providers=self.providers())
        dispatcher.clock = clock
        reply = dispatcher.handle_update(update())
        self.assertIn("PREDICTION_CLOSED", reply.text)
        self.assertIn("STALE_DATA", reply.text)

    def test_invalid_or_regressing_clock_never_releases_candidate(self):
        for readings in ((NOW.replace(tzinfo=None),), (NOW, NOW - timedelta(seconds=1))):
            dispatcher = self.dispatcher(providers=self.providers())
            dispatcher.clock = Mock(side_effect=readings)
            self.assertIn("CLOCK_UNAVAILABLE", dispatcher.handle_update(update()).text)

    def test_status_and_performance_never_call_provider(self):
        callback = Mock()
        dispatcher = self.dispatcher(providers=self.providers(callback))
        status = dispatcher.handle_update(update("/durum"))
        performance = dispatcher.handle_update(update("/performans"))
        self.assertIn("execution_enabled=false", status.text)
        self.assertIn("doğrulanmış sonuç yok", performance.text)
        callback.assert_not_called()

    def test_performance_does_not_claim_zero_or_perfect_for_unsettled_rows(self):
        dispatcher = self.dispatcher(providers=self.providers())
        dispatcher.handle_update(update())
        reply = dispatcher.handle_update(update("/performans", update_id=10))
        self.assertIn("bekleyen 1", reply.text)
        self.assertIn("fixture-v1: —", reply.text)
        self.assertNotIn("%100", reply.text)

    def test_equity_returns_watch_only_and_budget_labels(self):
        reply = self.dispatcher(providers=self.providers(sport="hisse")).handle_update(update("/hisse"))
        self.assertIn("İZLE", reply.text)
        self.assertIn("12.500 TL", reply.text)
        self.assertNotIn("AL sinyali", reply.text)

    def test_allowlists_and_provider_registration_cannot_be_disabled(self):
        for chats, users in ((set(), {200}), ({100}, set()), ({True}, {200}), ({100}, {False})):
            with self.assertRaisesRegex(ValueError, "INVALID_ACCESS_ALLOWLIST"):
                CommandDispatcher(self.store, allowed_chat_ids=chats, allowed_user_ids=users)
        with self.assertRaisesRegex(ValueError, "INVALID_PROVIDER_REGISTRATION"):
            self.dispatcher(providers={"at": lambda req: candidate()})

    def test_existing_receiver_bridge_handles_once_without_transport_calls(self):
        dispatcher = self.dispatcher()
        existing_send = Mock()
        fallback = Mock()
        # Only an in-memory test double stands in for the already-running receiver.
        reply = dispatcher.handle_update(update("/durum"))
        if reply is not None:
            existing_send(reply.chat_id, reply.text, parse_mode=reply.parse_mode)
        else:
            fallback()
        existing_send.assert_called_once()
        fallback.assert_not_called()


class FreshnessTests(unittest.TestCase):
    def reasons(self, value, sport="at"):
        return assess(value, sport, NOW, frozenset({"fixtures.example"}))[0]

    def test_each_branch_requires_every_mandatory_input(self):
        for sport in REQUIRED_EVIDENCE:
            original = candidate(sport)
            self.assertEqual(self.reasons(original, sport), ())
            for evidence in original.evidence:
                value = replace(original, evidence=tuple(e for e in original.evidence if e != evidence))
                self.assertIn("MANDATORY_DATA_MISSING", self.reasons(value, sport))

    def test_each_timestamp_stale_future_naive_and_unverified(self):
        original = candidate()
        for change, expected in (({"as_of": NOW - timedelta(days=2)}, "STALE_DATA"),
                                 ({"as_of": NOW + timedelta(seconds=1)}, "SOURCE_TIME_IN_FUTURE"),
                                 ({"as_of": NOW.replace(tzinfo=None)}, "SOURCE_TIME_UNVERIFIED"),
                                 ({"verified": False}, "SOURCE_UNVERIFIED"),
                                 ({"delay_seconds": None}, "SOURCE_DELAY_UNVERIFIED")):
            changed = replace(original.evidence[0], **change)
            self.assertIn(expected, self.reasons(replace(original, evidence=(changed,) + original.evidence[1:])))

    def test_scheduled_start_and_download_time_cannot_unlock_pr6(self):
        for status in ("OK", "PAS"):
            for agf in (0, 99):
                reader = lambda req: {"analysis": {"status": status, "modelVersion": "market-no-agf-v2",
                                     "picks": {"leader": {"number": 1, "agf": agf}}},
                                     "sourceTime": NOW.isoformat(), "updatedAt": NOW.isoformat()}
                value = PR6RaceProvider(reader)(None)
                self.assertIsNone(value.selection)
                self.assertIn("SOURCE_FRESHNESS_UNVERIFIED", self.reasons(value))

    def test_agf_or_duplicate_features_fail_closed(self):
        original = candidate()
        for evidence in (replace(original.evidence[0], name="agf"), original.evidence[0]):
            self.assertIn("UNSUPPORTED_OR_DUPLICATE_INPUT",
                          self.reasons(replace(original, evidence=original.evidence + (evidence,))))

    def test_source_url_credentials_query_or_unapproved_host_rejected(self):
        original = candidate()
        for url in ("https://u:p@fixtures.example/data", "https://fixtures.example/?key=synthetic",
                    "http://fixtures.example/data", "https://elsewhere.example/data", "https://[bad"):
            evidence = replace(original.evidence[0], source_url=url)
            reasons, provenance = assess(replace(original, evidence=(evidence,) + original.evidence[1:]),
                                         "at", NOW, frozenset({"fixtures.example"}))
            self.assertIn("SOURCE_UNVERIFIED", reasons)
            self.assertFalse(any(e["source_url"] == url for e in provenance))

    def test_delayed_quotes_are_not_realtime_even_if_recently_downloaded(self):
        original = candidate("hisse")
        for delay in (1, 900):
            evidence = tuple(replace(e, delay_seconds=delay) if e.name == "nbbo" else e for e in original.evidence)
            self.assertIn("REALTIME_QUOTES_UNVERIFIED", self.reasons(replace(original, evidence=evidence), "hisse"))


class StockRiskTests(unittest.TestCase):
    def test_exact_budget_and_spread_limits_pass_observational_gate(self):
        self.assertEqual(stock_reasons(risk()), [])

    def test_position_cap_and_capital_exposure(self):
        self.assertIn("POSITION_LIMIT", stock_reasons(risk(position_tl=D("12500.01"))))
        self.assertIn("CAPITAL_LIMIT", stock_reasons(risk(open_exposure_tl=D("37500.01"))))

    def test_daily_loss_threshold_and_planned_stop_budget(self):
        self.assertIn("DAILY_LOSS_LIMIT", stock_reasons(risk(daily_loss_tl=D("2500"))))
        self.assertEqual(stock_reasons(risk(daily_loss_tl=D("2125"))), [])
        self.assertIn("DAILY_RISK_BUDGET", stock_reasons(risk(daily_loss_tl=D("2125.01"))))

    def test_both_spread_limits_apply(self):
        self.assertIn("SPREAD_LIMIT", stock_reasons(risk(ask=D("2.05001"))))
        self.assertIn("SPREAD_LIMIT", stock_reasons(risk(bid=D("1"), ask=D("1.03"))))

    def test_price_volume_momentum_catalyst_sec_and_session(self):
        for changes, expected in (({"price": D("5.01")}, "PRICE_OUT_OF_RANGE"),
                                 ({"relative_volume": D("1.99")}, "WEAK_VOLUME_OR_MOMENTUM"),
                                 ({"momentum_percent": D("0")}, "WEAK_VOLUME_OR_MOMENTUM"),
                                 ({"positive_news": False}, "POSITIVE_NEWS_UNVERIFIED"),
                                 ({"sec_financing_clear": False}, "SEC_FINANCING_RISK_UNCLEARED"),
                                 ({"regular_session_open": False}, "MARKET_CLOSED")):
            self.assertIn(expected, stock_reasons(risk(**changes)))

    def test_invalid_numeric_and_crossed_or_missing_quotes_fail_closed(self):
        for value in (D("NaN"), D("Infinity"), D("1e1000"), D("1.000000001"), 2.0, True):
            self.assertIn("INVALID_RISK_DATA", stock_reasons(risk(price=value)))
        self.assertIn("INVALID_NBBO", stock_reasons(risk(bid=D("3"), ask=D("2"))))
        self.assertIn("RISK_DATA_UNAVAILABLE", stock_reasons(None))


if __name__ == "__main__":
    unittest.main()
