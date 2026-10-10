"""Synthetic bridge contracts; no Telegram, market or phone traffic."""
from dataclasses import replace
from datetime import timedelta
import unittest
import test_dispatcher as fixtures
from test_dispatcher import NOW, candidate, update
from tjk_commands import PR6RaceProvider
from tjk_commands.models import CommandRequest


class MissingIntegrationRegression(unittest.TestCase):
    setUp = fixtures.DispatcherTests.setUp
    dispatcher = fixtures.DispatcherTests.dispatcher
    def test_help_is_handled_by_shared_callback(self):
        reply = self.dispatcher().handle_update(update('/help'))
        self.assertIsNotNone(reply)
        self.assertIn('/at', reply.text)

    def test_pas_explains_missing_provider_in_turkish(self):
        reply = self.dispatcher().handle_update(update('/basket'))
        self.assertIn('Veri sağlayıcısı bağlı değil', reply.text)

    def test_fresh_bound_engine_prediction_can_cross_explicit_evidence_bridge(self):
        snapshot = {
            'date': '2026-10-08', 'venue': {'key': 'ANKARA'},
            'race': {'number': 1},
            'freshness': {'status': 'VERIFIED', 'valueBound': True},
            'analysis': {'status': 'OK', 'modelVersion': 'market-no-agf-v2',
                         'reasonCodes': [], 'picks': {'leader': {'number': 1}}},
            'runners': [{'number': i, 'currentOdds': i + 1, 'reportedCurrentOdds': i + 1, 'quoteAt': NOW.isoformat(),
                         'history': [{'at': NOW.timestamp()*1000, 'odds': i + 1}]} for i in (1, 2)],
        }
        # The reader is reviewed application code; evidence is never supplied by chat.
        from tjk_commands.engine_bridge import RaceEngineProvider, RaceSnapshot
        provider = RaceEngineProvider(lambda request: RaceSnapshot(snapshot, candidate().evidence,
                                                                  NOW + timedelta(minutes=10)))
        result = provider(CommandRequest('at', ('2026-10-08', 'ANKARA', '1'), NOW))
        self.assertTrue(result.inputs_complete)
        self.assertEqual(result.selection, '1')
        self.assertEqual(result.reason_codes, ())
        # The old PR6 adapter deliberately cannot do this.
        self.assertIn('SOURCE_FRESHNESS_UNVERIFIED', PR6RaceProvider(lambda req: snapshot)(CommandRequest('at', (), NOW)).reason_codes)


class EngineBridgeScenarios(unittest.TestCase):
    setUp = fixtures.DispatcherTests.setUp
    dispatcher = fixtures.DispatcherTests.dispatcher

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path
        import subprocess
        script = Path(__file__).parent / 'fixtures' / 'engine-snapshot.mjs'
        cls.snapshots = {number: json.loads(subprocess.run(['node', str(script), str(number)],
                        check=True, capture_output=True, text=True, timeout=20).stdout) for number in (5, 6)}

    def provider(self, snapshot=None, evidence=None):
        from tjk_commands.engine_bridge import RaceEngineProvider, RaceSnapshot
        return RaceEngineProvider(lambda request: RaceSnapshot(snapshot or self.snapshots[6],
                candidate().evidence if evidence is None else evidence, NOW + timedelta(hours=2)))

    def request(self):
        return CommandRequest('at', ('2026-10-08', 'BELMONT', '6'), NOW)

    def test_real_node_engine_belmont_5_and_6_have_separate_eight_active_runners(self):
        for number in (5, 6):
            snapshot = self.snapshots[number]
            self.assertEqual(snapshot['race']['number'], number)
            self.assertEqual(len(snapshot['runners']), 8)
            self.assertEqual(snapshot['analysis']['status'], 'OK')
            self.assertTrue(snapshot['freshness']['valueBound'])
        self.assertNotEqual(self.snapshots[5]['runners'][0]['name'], self.snapshots[6]['runners'][0]['name'])

    def test_actual_engine_prediction_is_pending_and_pas_is_separate(self):
        from tjk_commands import ProviderRegistration
        dispatcher = self.dispatcher(providers={'at': ProviderRegistration('market-no-agf-v2', self.provider(), frozenset({'fixtures.example'}))})
        reply = dispatcher.handle_update(update('/at 2026-10-08 BELMONT 6'))
        self.assertIn('TAHMİN', reply.text)
        self.assertIn('BEKLİYOR', reply.text)
        dispatcher.handle_update(update('/basket', update_id=8))
        rows = self.store.stats('100')
        horse = next(r for r in rows if r['sport'] == 'at')
        basket = next(r for r in rows if r['sport'] == 'basket')
        self.assertEqual((horse['pending'], horse['correct'], basket['pas']), (1, 0, 1))

    def test_verified_synthetic_result_settles_once_without_rewriting_prediction(self):
        from pathlib import Path
        from tjk_commands import PredictionStore, CommandDispatcher, ProviderRegistration, VerifiedResult
        cutoff = NOW + timedelta(hours=2)
        final_at = cutoff + timedelta(minutes=5)
        winner = str(self.snapshots[6]['analysis']['picks']['leader']['number'])
        # This verifier is deliberately a test double, not an official provider.
        verified = VerifiedResult('at', 'tjk:2026-10-08:BELMONT:6', 'winner', cutoff,
                                  'FINAL', winner, final_at, 'synthetic-results',
                                  'https://fixtures.example/results', final_at)
        store = PredictionStore(Path(self.tmp.name) / 'results-private' / 'ledger.sqlite3',
                                verifiers={'synthetic-results': lambda _: verified})
        self.addCleanup(store.close)
        dispatcher = CommandDispatcher(store, allowed_chat_ids={100}, allowed_user_ids={200},
            providers={'at': ProviderRegistration('market-no-agf-v2', self.provider(), frozenset({'fixtures.example'}))},
            clock=lambda: NOW)
        reply = dispatcher.handle_update(update('/at 2026-10-08 BELMONT 6'))
        self.assertIn('BEKLİYOR', reply.text)
        dispatcher.handle_update(update('/futbol', update_id=9))
        self.assertEqual(next(r for r in store.stats('100') if r['sport'] == 'at')['pending'], 1)
        store.record_result('synthetic-results', {}, now=final_at)
        store.record_result('synthetic-results', {}, now=final_at)
        rows = store.stats('100')
        horse = next(r for r in rows if r['sport'] == 'at')
        self.assertEqual((horse['correct'], horse['incorrect'], horse['pending']), (1, 0, 0))
        self.assertEqual(next(r for r in rows if r['sport'] == 'futbol')['pas'], 1)

    def test_no_source_provenance_does_not_turn_verified_into_a_live_pick(self):
        result = self.provider(evidence=())(self.request())
        self.assertIn('SOURCE_PROVENANCE_UNAVAILABLE', result.reason_codes)
        self.assertFalse(result.inputs_complete)

    def test_stale_61_second_quotes_rejected_even_when_node_says_verified(self):
        from copy import deepcopy
        snapshot = deepcopy(self.snapshots[6])
        old = NOW - timedelta(seconds=61)
        for runner in snapshot['runners']:
            runner['quoteAt'] = old.isoformat()
            runner['history'][-1]['at'] = old.timestamp() * 1000
        evidence = tuple(replace(e, as_of=old) if e.name == 'odds' else e for e in candidate().evidence)
        result = self.provider(snapshot, evidence)(self.request())
        self.assertIn('STALE_DATA', result.reason_codes)
        self.assertIsNone(result.selection)

    def test_binding_and_identity_checked_independently_of_verified_boolean(self):
        from copy import deepcopy
        snapshot = deepcopy(self.snapshots[6])
        snapshot['runners'][0]['currentOdds'] = 99
        self.assertIn('QUOTE_BINDING_UNVERIFIED', self.provider(snapshot)(self.request()).reason_codes)
        self.assertIn('RACE_IDENTITY_MISMATCH', self.provider(self.snapshots[5])(self.request()).reason_codes)

    def test_loopback_reader_reads_actual_engine_fixture_but_never_invents_evidence(self):
        import json
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from tjk_commands.engine_bridge import LocalRaceAnalysisReader
        payload = json.dumps(self.snapshots[6]).encode()
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            envelope = LocalRaceAnalysisReader(server.server_port)(self.request())
            self.assertEqual(envelope.evidence, ())
            self.assertEqual(envelope.analysis['analysis']['status'], 'OK')
            self.assertEqual(envelope.closes_at.isoformat(), '2026-10-08T22:57:00+03:00')
            self.assertEqual(paths, ['/api/race?date=2026-10-08&venue=BELMONT&race=6'])
        finally:
            server.shutdown(); server.server_close(); thread.join()


class EquityBridgeScenarios(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / 'termux-manager' / 'tests' / 'test_engine.py'
        spec = importlib.util.spec_from_file_location('fictional_equity_fixture', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.now = module.NOW
        self.bundle = module.fictional_bundle()

    def read(self, bundle=None):
        from tjk_commands.engine_bridge import EquityEngineProvider, EquitySnapshot
        from tjk_commands import SourceEvidence
        bundle = self.bundle if bundle is None else bundle
        security = bundle['securities'][0]
        stamps = {'prices': security['trade']['as_of'], 'nbbo': security['quote']['as_of'],
                  'volume': security['metrics']['as_of'], 'momentum': security['metrics']['as_of'],
                  'news': security['news']['published_at'], 'sec': security['filings']['checked_at'],
                  'fx': bundle['fx']['as_of'], 'account': bundle['account']['as_of']}
        from datetime import datetime
        evidence = tuple(SourceEvidence(name, 'fictional-only', 'https://fixtures.example/data',
                         datetime.fromisoformat(stamp), True, 0) for name, stamp in stamps.items())
        provider = EquityEngineProvider(lambda request: EquitySnapshot(bundle, 'fictional-event',
                    'close-up', self.now + timedelta(minutes=10), evidence))
        return provider(CommandRequest('hisse', (), self.now))

    def test_real_equity_guard_and_stricter_gates_both_pass_only_complete_synthetic_input(self):
        from tjk_commands.gates import assess
        result = self.read()
        self.assertTrue(result.inputs_complete, result.reason_codes)
        self.assertEqual(result.selection, 'FICT')
        self.assertEqual(assess(result, 'hisse', self.now, frozenset({'fixtures.example'}))[0], ())

    def test_missing_bid_ask_news_or_non_sip_feed_is_pas(self):
        from copy import deepcopy
        for field in ('bid', 'ask'):
            bundle = deepcopy(self.bundle)
            del bundle['securities'][0]['quote'][field]
            self.assertIn('EQUITY_ENGINE_PAS', self.read(bundle).reason_codes)
        bundle = deepcopy(self.bundle)
        bundle['securities'][0]['news']['status'] = 'unverified'
        self.assertIn('EQUITY_ENGINE_PAS', self.read(bundle).reason_codes)
        bundle = deepcopy(self.bundle)
        bundle['quote_source']['feed'] = 'iex'
        self.assertIn('EQUITY_ENGINE_PAS', self.read(bundle).reason_codes)

    def test_six_second_nbbo_cannot_bypass_shared_five_second_gate(self):
        from tjk_commands.gates import assess
        self.bundle['securities'][0]['quote']['as_of'] = (self.now - timedelta(seconds=6)).isoformat()
        result = self.read()
        self.assertIn('STALE_DATA', assess(result, 'hisse', self.now, frozenset({'fixtures.example'}))[0])

    def test_public_research_report_is_not_a_live_bundle(self):
        from tjk_commands.engine_bridge import EquityEngineProvider, EquitySnapshot
        provider = EquityEngineProvider(lambda request: EquitySnapshot({'decision': 'PAS', 'research_priority': ['FICT']}))
        result = provider(CommandRequest('hisse', (), self.now))
        self.assertIn('EQUITY_ENGINE_PAS', result.reason_codes)
        self.assertIsNone(result.selection)
