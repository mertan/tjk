"""Documented Fintable no-key prices; IEX observations, never SIP/NBBO.

https://fintable.io/docs documents these public endpoints. A successful HTTP
request establishes retrieval only: source timestamps and nullable fields are
preserved, unknown latency is never replaced by zero, and missing observations
are never replaced by history. All network access uses the bounded, read-only
PublicTransport. No credentials or environment variables are read here.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .providers import _iso
from .public_sources import PublicSourceError, PublicTransport

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
BASE_URL = "https://fintable.io/api/v2/prices"
DOCS_URL = "https://fintable.io/docs"
MAX_SYMBOLS = 20
MAX_BARS = 1000
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_SAFE_ERRORS = frozenset({
    "redirect_rejected", "source_access_stopped", "request_budget_exceeded",
    "collection_budget_exceeded", "response_too_large", "endpoint_rejected",
    "content_encoding_rejected", "network_unavailable", "source_request_failed",
    "fintable_encoding_invalid", "fintable_json_invalid", "fintable_schema_invalid",
    "fintable_symbol_duplicate", "fintable_symbol_unrequested", "fintable_symbol_missing",
    "fintable_symbol_invalid", "fintable_symbols_invalid", "fintable_date_invalid",
    "fintable_timestamp_invalid", "fintable_history_empty", "fintable_history_truncated",
    "fintable_bar_duplicate", "fintable_bar_date_mismatch", "fintable_bar_invalid",
})


def _safe_error(exc):
    code = exc.code if isinstance(exc, PublicSourceError) else "source_request_failed"
    return code if isinstance(code, str) and (code in _SAFE_ERRORS or
        re.fullmatch(r"http_[1-5][0-9]{2}", code)) else "source_request_failed"


def _symbol(value):
    return isinstance(value, str) and bool(SYMBOL.fullmatch(value))


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PublicSourceError("fintable_date_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PublicSourceError("fintable_date_invalid") from None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        raise PublicSourceError("fintable_timestamp_invalid")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
        return instant
    except (ValueError, OverflowError):
        raise PublicSourceError("fintable_timestamp_invalid") from None


def _price(value):
    if (not isinstance(value, str) or len(value) > 48 or
            not _DECIMAL.fullmatch(value) or Decimal(value) <= 0):
        raise PublicSourceError("fintable_schema_invalid")
    return value


def _volume(value):
    if type(value) is not int or not 0 <= value <= 10 ** 18:
        raise PublicSourceError("fintable_schema_invalid")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicSourceError("fintable_json_invalid")
        result[key] = value
    return result


def _constant(value):
    raise PublicSourceError("fintable_json_invalid")


def _parse_quote(row):
    fields = {"symbol", "price", "currency", "previous_close", "volume", "trading_day", "as_of", "feed"}
    if (not isinstance(row, dict) or not fields.issubset(row) or
            not _symbol(row["symbol"]) or row["currency"] != "USD" or row["feed"] != "iex"):
        raise PublicSourceError("fintable_schema_invalid")
    quote = {key: row[key] for key in fields}
    quote["price"] = _price(quote["price"])
    errors = []
    for field, validator in (("previous_close", _price), ("volume", _volume),
                             ("trading_day", _day), ("as_of", _timestamp)):
        if quote[field] is None:
            errors.append("fintable_" + ("timestamp" if field == "as_of" else field) + "_missing")
        else:
            validator(quote[field])
    quote["errors"] = errors
    return quote


def _parse_history(payload, symbol, day):
    fields = {"symbol", "timeframe", "currency", "feed", "bars"}
    if (not isinstance(payload, dict) or not fields.issubset(payload) or
            payload["symbol"] != symbol or payload["timeframe"] != "5min" or
            payload["currency"] != "USD" or payload["feed"] != "iex" or
            not isinstance(payload["bars"], list) or len(payload["bars"]) > MAX_BARS):
        raise PublicSourceError("fintable_schema_invalid")
    # Reaching the requested limit is conservatively incomplete: no implicit
    # pagination and no claim that omitted bars did not exist.
    if len(payload["bars"]) == MAX_BARS:
        raise PublicSourceError("fintable_history_truncated")
    fields = {"timestamp", "date", "open", "high", "low", "close", "volume", "trade_count", "vwap"}
    bars, seen = [], set()
    for row in payload["bars"]:
        if not isinstance(row, dict) or not fields.issubset(row):
            raise PublicSourceError("fintable_schema_invalid")
        bar = {key: row[key] for key in fields}
        instant = _timestamp(bar["timestamp"])
        if instant in seen:
            raise PublicSourceError("fintable_bar_duplicate")
        seen.add(instant)
        bar_day = _day(bar["date"])
        if (bar_day not in {day, day + timedelta(days=1)} or
                instant.astimezone(NY).date() != bar_day):
            raise PublicSourceError("fintable_bar_date_mismatch")
        for key in ("open", "high", "low", "close", "vwap"):
            _price(bar[key])
        for key in ("volume", "trade_count"):
            _volume(bar[key])
        low, high = Decimal(bar["low"]), Decimal(bar["high"])
        if low > high or any(not low <= Decimal(bar[key]) <= high for key in ("open", "close", "vwap")):
            raise PublicSourceError("fintable_bar_invalid")
        # The API documents first/last dates without boundary semantics. The
        # requested end is the following day; retain only the requested session.
        if bar_day == day:
            bars.append(bar)
    bars.sort(key=lambda bar: _timestamp(bar["timestamp"]))
    return {"symbol": symbol, "timeframe": "5min", "currency": "USD", "feed": "iex", "bars": bars}


class FintableSource:
    """No-key public market data with injectable transport for offline tests.

    Share one PublicTransport with directory/SEC retrieval to enforce one
    collection budget. Status ``available`` means complete parseable evidence,
    not current or executable market data; freshness is assessed by the caller.
    ``partial`` snapshots retain valid rows and explicitly missing fields.
    """

    def __init__(self, transport=None, now=None):
        self.transport = transport if transport is not None else PublicTransport()
        self.fixed_now = now
        self.sources = []

    def _now(self):
        return self.fixed_now if self.fixed_now is not None else datetime.now(UTC)

    def _source(self, url):
        return {"provider": "Fintable", "url": url, "docs_url": DOCS_URL,
                "status": "unavailable", "attempted_at": _iso(self._now()),
                "retrieved_at": None, "source_timestamp": None,
                "delay_seconds": None, "freshness_verified": False, "feed": "iex",
                "coverage": "IEX_ONLY_NOT_CONSOLIDATED", "executable_nbbo": False,
                "historical_adjustment": "split_and_dividend_adjusted"}

    def _get(self, url, source):
        raw = self.transport.get(url, {"User-Agent": "tjk-public-analysis/1.0",
                                      "Accept": "application/json", "Accept-Encoding": "identity"})
        source["retrieved_at"] = _iso(self._now())
        try:
            decoded = raw.decode("utf-8-sig")
        except (AttributeError, UnicodeError):
            raise PublicSourceError("fintable_encoding_invalid") from None
        try:
            payload = json.loads(decoded, object_pairs_hook=_object, parse_constant=_constant)
        except (ValueError, RecursionError):
            raise PublicSourceError("fintable_json_invalid") from None
        if not isinstance(payload, dict) or "data" not in payload:
            raise PublicSourceError("fintable_schema_invalid")
        return payload["data"]

    def _failure(self, exc, source, **empty):
        code = _safe_error(exc)
        if source is not None:
            source.update(status="unavailable", error=code)
            self.sources.append(source)
        return {"status": "unavailable", **empty, "sources": [source] if source is not None else [], "errors": [code]}

    def prices(self, symbols):
        """Return symbol-keyed observations and explicit partial/missing data."""
        source = None
        try:
            if (not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= MAX_SYMBOLS or
                    any(not _symbol(symbol) for symbol in symbols) or len(set(symbols)) != len(symbols)):
                raise PublicSourceError("fintable_symbols_invalid")
            url = BASE_URL + "?symbols=" + ",".join(symbols)
            source = self._source(url)
            rows = self._get(url, source)
            if not isinstance(rows, list) or len(rows) > MAX_SYMBOLS:
                raise PublicSourceError("fintable_schema_invalid")
            quotes = {}
            for row in rows:
                quote = _parse_quote(row)
                symbol = quote["symbol"]
                if symbol not in symbols:
                    raise PublicSourceError("fintable_symbol_unrequested")
                if symbol in quotes:
                    raise PublicSourceError("fintable_symbol_duplicate")
                quotes[symbol] = quote
            missing = [symbol for symbol in symbols if symbol not in quotes]
            errors = ["fintable_symbol_missing"] if missing else []
            errors.extend(error for quote in quotes.values() for error in quote["errors"])
            status = ("partial" if errors else "available") if quotes else "unavailable"
            source["status"] = status
            # Mixed batches have no single observation time; keep it per symbol.
            if len(quotes) == 1:
                source["source_timestamp"] = next(iter(quotes.values()))["as_of"]
            self.sources.append(source)
            return {"status": status, "quotes": quotes, "missing_symbols": missing,
                    "sources": [source], "errors": list(dict.fromkeys(errors))}
        except Exception as exc:
            return self._failure(exc, source, quotes={}, missing_symbols=[])

    def history(self, symbol, day):
        """Retrieve one trading day's adjusted five-minute IEX bars, no paging."""
        source = None
        try:
            if not _symbol(symbol):
                raise PublicSourceError("fintable_symbol_invalid")
            requested_day = _day(day)
            end = (requested_day + timedelta(days=1)).isoformat()
            url = (BASE_URL + "/" + symbol + "/history?timeframe=5min&start=" + day +
                   "&end=" + end + "&limit=1000")
            source = self._source(url)
            data = _parse_history(self._get(url, source), symbol, requested_day)
            if not data["bars"]:
                raise PublicSourceError("fintable_history_empty")
            source.update(status="available", source_timestamp=data["bars"][-1]["timestamp"])
            self.sources.append(source)
            return {"status": "available", "data": data, "sources": [source], "errors": []}
        except Exception as exc:
            return self._failure(exc, source, data=None)
