"""Read existing engine snapshots. No listener, credentials, polling or execution.

Readers are reviewed application callbacks, never deserializers for chat input.
A current engine result cannot supply missing source-time provenance by itself.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
import math
import re

from .gates import aware
from .models import Candidate, SourceEvidence, StockRiskInputs


@dataclass(frozen=True)
class RaceSnapshot:
    analysis: dict
    evidence: tuple[SourceEvidence, ...] = ()
    closes_at: datetime | None = None


@dataclass(frozen=True)
class EquitySnapshot:
    bundle: dict
    event_id: str | None = None
    market: str | None = None
    closes_at: datetime | None = None
    evidence: tuple[SourceEvidence, ...] = ()


def _time(value):
    try:
        value = datetime.fromisoformat(value)
        return value if aware(value) else None
    except (TypeError, ValueError):
        return None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class RaceEngineProvider:
    """Use the existing Node market-no-agf-v2 output without scoring it again.

    Explicit /at YYYY-MM-DD VENUE RACE selects exactly one race. The existing
    receiver supplies its engine reader and independently established input
    evidence; no schedule/download/heartbeat time is promoted to source time.
    """
    def __init__(self, snapshot_reader):
        self._reader = snapshot_reader

    def __call__(self, request):
        if (len(request.args) != 3 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', request.args[0])
                or not re.fullmatch(r'[A-Z0-9_-]{1,40}', request.args[1])
                or not re.fullmatch(r'[1-9][0-9]?', request.args[2])):
            return Candidate(reason_codes=('INVALID_COMMAND_ARGUMENTS',))
        try:
            datetime.strptime(request.args[0], '%Y-%m-%d')
            envelope = self._reader(request)
            if not isinstance(envelope, RaceSnapshot):
                return Candidate(reason_codes=('SOURCE_PROVENANCE_UNAVAILABLE',))
            snapshot = envelope.analysis
            analysis = snapshot['analysis']
            if (snapshot['date'], snapshot['venue']['key'], str(snapshot['race']['number'])) != request.args:
                return Candidate(reason_codes=('RACE_IDENTITY_MISMATCH',))
            base = Candidate(event_id='tjk:' + ':'.join(request.args), market='winner',
                             closes_at=envelope.closes_at, evidence=envelope.evidence)
            reasons = []
            if analysis.get('modelVersion') != 'market-no-agf-v2':
                reasons.append('MODEL_UNVERIFIED')
            if analysis.get('status') != 'OK' or analysis.get('reasonCodes') != []:
                reasons.append('UPSTREAM_PAS')
            freshness = snapshot.get('freshness', {})
            if freshness.get('status') != 'VERIFIED' or freshness.get('valueBound') is not True:
                reasons.append('SOURCE_FRESHNESS_UNVERIFIED')
            runners = snapshot.get('runners')
            if not isinstance(runners, list) or not 2 <= len(runners) <= 30:
                return replace(base, reason_codes=tuple(reasons + ['RUNNERS_UNVERIFIED']))
            quote_times, numbers = [], set()
            for runner in runners:
                number = runner.get('number')
                if type(number) is not int or not 1 <= number <= 30 or number in numbers:
                    reasons.append('RUNNERS_UNVERIFIED')
                numbers.add(number)
                history = runner.get('history')
                quote_at = _time(runner.get('quoteAt'))
                current = runner.get('currentOdds')
                if (not isinstance(history, list) or not history or not quote_at
                        or not _number(current) or not 0 < current < 900
                        or any(not isinstance(p, dict) or not _number(p.get('at')) or not _number(p.get('odds')) for p in history)):
                    reasons.append('QUOTE_BINDING_UNVERIFIED')
                    continue
                latest = max(history, key=lambda p: p['at'])
                if (latest['at'] != quote_at.timestamp() * 1000 or latest['odds'] != current or runner.get('reportedCurrentOdds') != current
                        or any(p['at'] == latest['at'] and p['odds'] != current for p in history)):
                    reasons.append('QUOTE_BINDING_UNVERIFIED')
                age = (request.now - quote_at).total_seconds()
                if age < 0 or age > 60:
                    reasons.append('STALE_DATA')
                quote_times.append(quote_at)
            odds = [e for e in envelope.evidence if isinstance(e, SourceEvidence) and e.name == 'odds']
            if len(odds) != 1 or not quote_times or odds[0].as_of != min(quote_times):
                reasons.append('SOURCE_PROVENANCE_UNAVAILABLE')
            selected = analysis.get('picks', {}).get('leader', {})
            selection = selected.get('number') if isinstance(selected, dict) else None
            if type(selection) is not int or selection not in numbers:
                reasons.append('MISSING_PREDICTION_DATA')
            return replace(base, selection=str(selection) if not reasons else None,
                           inputs_complete=not reasons, reason_codes=tuple(dict.fromkeys(reasons)))
        except Exception:
            return Candidate(reason_codes=('INVALID_PROVIDER_DATA',))


class EquityEngineProvider:
    """Apply existing equity_guard first, then the stricter shared source gates.

    A public_scan research report is not a complete live engine bundle. Fintable
    alone cannot pass this adapter. No existing risk or source gate is relaxed.
    """
    def __init__(self, snapshot_reader):
        self._reader = snapshot_reader

    def __call__(self, request):
        try:
            from equity_guard.engine import evaluate
            snapshot = self._reader(request)
            if not isinstance(snapshot, EquitySnapshot):
                return Candidate(reason_codes=('SOURCE_PROVENANCE_UNAVAILABLE',))
            base = Candidate(event_id=snapshot.event_id, market=snapshot.market,
                             closes_at=snapshot.closes_at, evidence=snapshot.evidence)
            result = evaluate(snapshot.bundle, now=request.now)
            if result.get('decision') != 'HAZIRLIK' or result.get('execution_enabled') is not False:
                return replace(base, reason_codes=('EQUITY_ENGINE_PAS',))
            picks = result.get('candidates', [])
            if len(picks) != 1:
                return replace(base, reason_codes=('EQUITY_ENGINE_PAS',))
            pick = picks[0]
            bundle = snapshot.bundle
            matches = [s for s in bundle['securities'] if s['symbol'] == pick['symbol']]
            if len(matches) != 1:
                return replace(base, reason_codes=('EQUITY_ENGINE_PAS',))
            security = matches[0]
            quote, trade, metrics = security['quote'], security['trade'], security['metrics']
            expected = {'prices': trade['as_of'], 'nbbo': quote['as_of'], 'volume': metrics['as_of'],
                        'momentum': metrics['as_of'], 'news': security['news']['published_at'],
                        'sec': security['filings']['checked_at'], 'fx': bundle['fx']['as_of'],
                        'account': bundle['account']['as_of']}
            evidence = {e.name: e for e in snapshot.evidence if isinstance(e, SourceEvidence)}
            if any(name not in evidence or evidence[name].as_of != _time(stamp) for name, stamp in expected.items()):
                return replace(base, reason_codes=('SOURCE_PROVENANCE_UNAVAILABLE',))
            risk = StockRiskInputs(
                Decimal(str(trade['price'])), Decimal(str(quote['bid'])), Decimal(str(quote['ask'])),
                Decimal(pick['usdtry_ask']), Decimal(pick['entry_debit_try']),
                sum((Decimal(str(p['market_value_try'])) for p in bundle['account']['positions']), Decimal(0)) + Decimal(str(bundle['account']['reserved_try'])),
                Decimal(pick['daily_loss_try']), Decimal(str(metrics['rvol_5m'])),
                Decimal(str(metrics['return_5m_pct'])), True, True, True)
            return replace(base, selection=pick['symbol'], stock_risk=risk, inputs_complete=True)
        except ImportError:
            return Candidate(reason_codes=('EQUITY_ENGINE_UNAVAILABLE',))
        except Exception:
            return Candidate(reason_codes=('INVALID_PROVIDER_DATA',))


class LocalRaceAnalysisReader:
    """Read an already-running loopback TJK engine; never starts a service.

    Only a reviewed evidence_reader can add independently verified source
    metadata. The normal Node response currently lacks complete provenance,
    so without it the dispatcher records an explanatory PAS.
    """
    def __init__(self, port, *, evidence_reader=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('INVALID_ENGINE_PORT')
        self._port = port
        self._evidence_reader = evidence_reader

    def __call__(self, request):
        import http.client
        import json
        from urllib.parse import urlencode
        if len(request.args) != 3:
            raise ValueError('INVALID_COMMAND_ARGUMENTS')
        query = urlencode(dict(zip(('date', 'venue', 'race'), request.args)))
        connection = http.client.HTTPConnection('127.0.0.1', self._port, timeout=10)
        try:
            connection.request('GET', '/api/race?' + query, headers={'Accept': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('ENGINE_UNAVAILABLE')
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('ENGINE_RESPONSE_TOO_LARGE')
            snapshot = json.loads(raw)
        finally:
            connection.close()
        evidence = self._evidence_reader(snapshot, request) if self._evidence_reader else ()
        # A schedule is used only as event cutoff, never as quote/source time.
        time = snapshot.get('race', {}).get('time', '')
        closes = None
        if isinstance(time, str) and re.fullmatch(r'\d{1,2}[.:]\d{2}', time):
            hour, minute = map(int, re.split(r'[.:]', time))
            closes = _time(f'{request.args[0]}T{hour:02d}:{minute:02d}:00+03:00')
        return RaceSnapshot(snapshot, evidence, closes)
