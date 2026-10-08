"""Read-only, fail-closed Alpaca SIP / SEC adapter (Python standard library).

Only an explicitly configured universe (maximum 20 symbols) is covered. This is
not a whole-market scanner. No endpoint in this module can submit an order.
Historical values are never substituted for unavailable current quotes.

``collect`` can be tested with ``unittest.mock.patch`` on ``get_json``; the
``ReadOnlyProvider`` class also accepts an injected JSON transport and env map.
"""

from __future__ import annotations

import json
import math
import os
import re
import ssl
import threading
import time as elapsed_time
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
MAX_BODY = 8 * 1024 * 1024
MAX_PAGES = 8
MAX_SYMBOLS = 20
COLLECTION_BUDGET_SECONDS = 60
SEC_MIN_INTERVAL_SECONDS = 0.5
_SEC_LOCK = threading.Lock()
_SEC_NEXT_REQUEST = 0.0
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
SEC_FILE_RE = re.compile(r"^CIK\d{10}-submissions-\d{3,}\.json$")
# SEC EDGAR submission types include automatic shelves, Rule 462(b)
# additional-securities registrations and post-effective shelf amendments.
# None of these may inherit a manual "clear" merely because the shorter
# S-3/F-3 submission names were the only names recognized by the adapter.
REGISTRATION_FORMS = frozenset({
    "S-1", "S-1/A", "S-1MEF", "S-3", "S-3/A", "S-3ASR", "S-3D", "S-3DPOS", "S-3MEF",
    "F-1", "F-1/A", "F-1MEF", "F-3", "F-3/A", "F-3ASR", "F-3D", "F-3DPOS", "F-3MEF",
    "POS AM", "POSASR", "EFFECT",
})
DATA = "https://data.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"
SEC = "https://data.sec.gov"


class ProviderError(Exception):
    """Only fixed, non-secret diagnostic messages may be used here."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("redirect_rejected")


def _allowed_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    routes = {
        "data.alpaca.markets": {"/v2/stocks/snapshots", "/v2/stocks/bars", "/v1beta1/news"},
        "paper-api.alpaca.markets": {"/v2/clock", "/v2/assets", "/v2/calendar"},
    }
    if parsed.hostname in routes:
        return parsed.path in routes[parsed.hostname]
    if parsed.hostname == "data.sec.gov":
        return bool(re.fullmatch(r"/submissions/CIK\d{10}(?:-submissions-\d{3,})?\.json", parsed.path))
    return False


def _remaining_timeout(deadline):
    if deadline is None:
        return 12
    remaining = deadline - elapsed_time.monotonic()
    if remaining <= 0:
        raise ProviderError("collection_budget_exceeded")
    return min(12, remaining)


def _pace_sec(deadline):
    """One process-wide SEC request every >=0.5 sec, including retries."""
    global _SEC_NEXT_REQUEST
    with _SEC_LOCK:
        _remaining_timeout(deadline)
        wait = max(0, _SEC_NEXT_REQUEST - elapsed_time.monotonic())
        if wait:
            if deadline is not None and elapsed_time.monotonic() + wait >= deadline:
                raise ProviderError("collection_budget_exceeded")
            elapsed_time.sleep(wait)
        _remaining_timeout(deadline)
        _SEC_NEXT_REQUEST = elapsed_time.monotonic() + SEC_MIN_INTERVAL_SECONDS


def _bounded_body(response, deadline):
    chunks, size = [], 0
    while True:
        timeout = _remaining_timeout(deadline)
        # HTTPSResponse exposes the actual TLS socket through its buffered
        # reader. Refresh its timeout so slow response bodies cannot repeatedly
        # reset the full original timeout beyond the collection deadline.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(timeout)
        reader = getattr(response, "read1", response.read)
        chunk = reader(min(65536, MAX_BODY + 1 - size))
        _remaining_timeout(deadline)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_BODY:
            raise ProviderError("response_too_large")
        chunks.append(chunk)


def get_json(url: str, headers: dict, params: dict | None = None, *, deadline=None):
    """GET an exact allowlisted HTTPS route, inheriting OS HTTP(S) proxy.

    Default TLS verification, no redirects, <=12-second timeout, 8 MiB cap and
    at most one retry for transient transport / HTTP 429 or 5xx failures.
    Responses and request headers are never logged.
    """
    try:
        allowed = _allowed_url(url)
    except (TypeError, ValueError):
        allowed = False
    if not allowed:
        raise ProviderError("endpoint_rejected")
    # Keys can never be sent to SEC, even by an accidental internal caller.
    if urlsplit(url).hostname == "data.sec.gov" and any(k.lower().startswith("apca-") for k in headers):
        raise ProviderError("credential_scope_rejected")
    full_url = url + ("?" + urlencode(params) if params else "")
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    for attempt in range(2):
        try:
            if urlsplit(url).hostname == "data.sec.gov":
                _pace_sec(deadline)
            request = Request(full_url, headers=headers, method="GET")
            with opener.open(request, timeout=_remaining_timeout(deadline)) as response:
                if response.status != 200:
                    raise ProviderError("unexpected_http_status")
                raw = _bounded_body(response, deadline)
            decoded = json.loads(raw)
            if not isinstance(decoded, (dict, list)):
                raise ProviderError("invalid_json_shape")
            _remaining_timeout(deadline)
            return decoded
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if attempt == 0 and (code == 429 or 500 <= code <= 599):
                continue
            raise ProviderError("http_" + str(code)) from None
        except (URLError, TimeoutError, OSError):
            if attempt == 0:
                continue
            raise ProviderError("network_unavailable") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_json") from None


def _dt(value):
    if not isinstance(value, str):
        raise ProviderError("timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderError("timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise ProviderError("timestamp_timezone_missing")
    return parsed.astimezone(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _num(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError("numeric_field_missing")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ProviderError("numeric_field_invalid")
    return value


def _session(day, field):
    # Alpaca calendar times are America/New_York, not UTC.
    try:
        return datetime.combine(date.fromisoformat(day["date"]), time.fromisoformat(day[field]), NY)
    except (KeyError, ValueError, TypeError):
        raise ProviderError("calendar_invalid") from None


def _validate_calendar(calendar):
    """One row per actual session; duplicated dates cannot inflate RVOL history."""
    if not isinstance(calendar, list) or not calendar:
        raise ProviderError("calendar_invalid")
    seen = set()
    for day in calendar:
        if not isinstance(day, dict):
            raise ProviderError("calendar_invalid")
        opened, closed = _session(day, "open"), _session(day, "close")
        if (opened >= closed or opened.second or opened.microsecond or
                closed.second or closed.microsecond or opened.minute % 5 or closed.minute % 5):
            raise ProviderError("calendar_invalid")
        session_date = opened.date()
        if session_date in seen:
            raise ProviderError("calendar_duplicate_session")
        seen.add(session_date)


def _metrics(bars, calendar, now, trade, previous_close):
    """Same-clock-slot 5-minute RVOL against ten prior complete sessions.

    Every regular-session slot through the matching slot must exist, for both
    today and ten previous eligible sessions. Partial or absent bars fail.
    Daily metrics use completed regular-session bars only (conservative volume).
    """
    _validate_calendar(calendar)
    current = now.astimezone(NY)
    slot = current.replace(minute=(current.minute // 5) * 5, second=0, microsecond=0) - timedelta(minutes=5)
    today = next((d for d in calendar if d.get("date") == current.date().isoformat()), None)
    if not today or slot < _session(today, "open") or slot + timedelta(minutes=5) > _session(today, "close"):
        raise ProviderError("complete_regular_session_bar_unavailable")
    indexed = {}
    for bar in bars:
        stamp = _dt(bar.get("t")).astimezone(NY)
        if stamp.second or stamp.microsecond or stamp.minute % 5:
            raise ProviderError("bar_timestamp_invalid")
        if stamp in indexed:
            raise ProviderError("duplicate_bar")
        # Validate all inputs used in metrics; no impossible OHLC / VWAP.
        for field in ("o", "h", "l", "c", "vw"):
            _num(bar.get(field), positive=True)
        _num(bar.get("v"))
        if not bar["l"] <= min(bar["o"], bar["c"], bar["vw"]) <= max(bar["o"], bar["c"], bar["vw"]) <= bar["h"]:
            raise ProviderError("bar_price_invalid")
        indexed[stamp] = bar

    def through(day):
        target = datetime.combine(date.fromisoformat(day["date"]), slot.timetz().replace(tzinfo=None), NY)
        start = _session(day, "open")
        if target < start or target + timedelta(minutes=5) > _session(day, "close"):
            return None
        count = int((target - start).total_seconds() // 300) + 1
        stamps = [start + timedelta(minutes=5 * i) for i in range(count)]
        if any(stamp not in indexed for stamp in stamps):
            raise ProviderError("historical_or_current_bar_gap")
        return [indexed[stamp] for stamp in stamps]

    current_bars = through(today)
    historical = []
    for day in sorted(calendar, key=lambda d: d.get("date", ""), reverse=True):
        if day.get("date", "") >= today["date"]:
            continue
        values = through(day)
        if values is not None:
            historical.append(values[-1]["v"])
        if len(historical) == 10:
            break
    if len(historical) < 10 or mean(historical) <= 0:
        raise ProviderError("insufficient_matched_historical_sessions")
    volume = sum(b["v"] for b in current_bars)
    if volume <= 0:
        raise ProviderError("regular_session_volume_unavailable")
    dollar_volume = sum(b["v"] * b["vw"] for b in current_bars)
    last = current_bars[-1]
    return {
        "as_of": _iso(now),
        "window_end": _iso(slot + timedelta(minutes=5)),
        "day_volume": volume,
        "day_dollar_volume": dollar_volume,
        "rvol_5m": last["v"] / mean(historical),
        "return_5m_pct": 100 * (last["c"] / last["o"] - 1),
        "day_change_pct": 100 * (trade / previous_close - 1),
        "vwap": dollar_volume / volume,
        "rvol_sessions": len(historical),
        "rvol_method": "same_NY_5min_slot",
        "daily_scope": "completed_regular_session_5min_bars",
    }


def _previous_adjusted_close(bars, calendar, now):
    current_date = now.astimezone(NY).date().isoformat()
    previous = max((d for d in calendar if d["date"] < current_date), key=lambda d: d["date"], default=None)
    if previous is None:
        raise ProviderError("previous_adjusted_close_unavailable")
    last_slot = _session(previous, "close") - timedelta(minutes=5)
    matches = [bar for bar in bars if _dt(bar.get("t")) == last_slot]
    if len(matches) != 1:
        raise ProviderError("previous_adjusted_close_unavailable")
    return _num(matches[0].get("c"), positive=True)


class ReadOnlyProvider:
    def __init__(self, getter=None, environ=None):
        self.getter = getter or get_json
        self.standard_transport = getter is None
        self.env = os.environ if environ is None else environ
        self._deadline = None

    def _get(self, url, headers, params=None):
        # Defense in depth for injected transports and future call-site changes.
        if not _allowed_url(url):
            raise ProviderError("endpoint_rejected")
        try:
            _remaining_timeout(self._deadline)
            if self.standard_transport:
                result = self.getter(url, headers, params or {}, deadline=self._deadline)
            else:
                result = self.getter(url, headers, params or {})
            _remaining_timeout(self._deadline)
            return result
        except ProviderError:
            raise
        except Exception:
            # Injected transports must not leak an exception containing a key.
            raise ProviderError("provider_request_failed") from None

    def _bars(self, symbol, headers, now):
        # The latest bucket may still be open while this request is made.
        # Never let a later news/filings request crossing a five-minute boundary
        # turn this partial observation into an apparently completed bar.
        completed_through = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        params = {"symbols": symbol, "timeframe": "5Min", "start": _iso(now - timedelta(days=45)),
                  "end": _iso(now), "adjustment": "split", "feed": "sip", "limit": 10000, "sort": "asc"}
        bars, tokens = [], set()
        for _ in range(MAX_PAGES):
            payload = self._get(DATA + "/v2/stocks/bars", headers, params)
            batch = payload.get("bars", {}).get(symbol, [])
            if not isinstance(batch, list):
                raise ProviderError("bars_schema_invalid")
            bars.extend(batch)
            token = payload.get("next_page_token")
            if not token:
                # Enforce the cutoff independently of upstream end semantics.
                return [bar for bar in bars
                        if _dt(bar.get("t")) + timedelta(minutes=5) <= completed_through]
            if not isinstance(token, str) or len(token) > 2048 or token in tokens:
                raise ProviderError("pagination_invalid")
            tokens.add(token)
            params["page_token"] = token
        raise ProviderError("pagination_limit_reached")

    def _news(self, entry, headers, now):
        review = entry.get("news_review") or {}
        params = {"symbols": entry["symbol"], "start": _iso(now - timedelta(days=2)),
                  "end": _iso(now), "limit": 50, "sort": "desc", "include_content": "false"}
        payload = self._get(DATA + "/v1beta1/news", headers, params)
        candidates = []
        for article in payload.get("news", [])[:50]:
            if (str(article.get("source", "")).lower() != "benzinga" or
                    entry["symbol"] not in article.get("symbols", []) or
                    not str(article.get("url", "")).startswith("https://") or not article.get("headline")):
                continue
            try:
                published, updated = _dt(article.get("created_at")), _dt(article.get("updated_at"))
            except ProviderError:
                continue
            if not now - timedelta(days=1) <= published <= updated <= now:
                continue
            candidates.append({"id": str(article.get("id")), "url": article["url"], "published_at": _iso(published),
                               "updated_at": _iso(updated), "title": article["headline"], "source": "Benzinga"})
            if len(candidates) == 20:
                break
        if review.get("status") != "verified":
            return {"status": "unknown", "review_candidates": candidates}
        # One bounded page is enough to verify an explicit recent article. If
        # the reviewed article is absent, do not assume pagination contains it.
        for article in payload.get("news", []):
            if str(article.get("id")) != str(review.get("id")):
                continue
            if (article.get("url") != review.get("url") or
                    str(article.get("source", "")).lower() != "benzinga" or
                    entry["symbol"] not in article.get("symbols", [])):
                raise ProviderError("news_review_source_mismatch")
            published = _dt(article.get("created_at"))
            updated = _dt(article.get("updated_at"))
            reviewed = _dt(review.get("reviewed_at"))
            if (published != _dt(review.get("published_at")) or
                    not published <= updated <= reviewed <= now):
                raise ProviderError("news_review_time_mismatch")
            if not article.get("headline") or not str(article.get("url", "")).startswith("https://"):
                raise ProviderError("news_provenance_invalid")
            return {"status": "verified", "published_at": _iso(published), "reviewed_at": _iso(reviewed),
                    "url": article["url"], "title": article["headline"], "category": review.get("category"),
                    "material": review.get("material") is True, "source": "Benzinga", "id": str(article["id"])}
        return {"status": "unknown", "review_candidates": candidates}

    def _filings(self, entry, now):
        unknown = {"status": "unknown", "flags": [], "coverage_days": 0}
        agent = self.env.get("SEC_USER_AGENT", "").strip()
        cik = str(entry.get("cik", ""))
        if not agent or not re.fullmatch(r"\d{1,10}", cik):
            return unknown
        cik = cik.zfill(10)
        source_url = SEC + "/submissions/CIK" + cik + ".json"
        headers = {"User-Agent": agent, "Accept": "application/json", "Accept-Encoding": "identity"}
        payload = self._get(source_url, headers)
        if entry["symbol"] not in payload.get("tickers", []):
            raise ProviderError("sec_symbol_cik_mismatch")
        cutoff = (now - timedelta(days=365)).date().isoformat()
        filings = payload.get("filings", {})
        tables = [filings.get("recent", {})]
        recent_dates = tables[0].get("filingDate", [])
        if not recent_dates:
            return unknown
        files = [f for f in filings.get("files", []) if f.get("filingTo", "") >= cutoff]
        if len(files) > MAX_PAGES:
            raise ProviderError("sec_pagination_limit_reached")
        # SEC lists are all consumed within coverage, even if recent reaches
        # the cutoff, so overlapping archived metadata cannot be silently lost.
        for item in files:
            filename = item.get("name", "")
            if not SEC_FILE_RE.fullmatch(filename) or not filename.startswith("CIK" + cik):
                raise ProviderError("sec_archive_path_invalid")
            tables.append(self._get(SEC + "/submissions/" + filename, headers))
        all_dates, recent_rows = [], []
        for table in tables:
            dates = table.get("filingDate", [])
            forms = table.get("form", [])
            if len(forms) != len(dates):
                raise ProviderError("sec_schema_invalid")
            for i, filing_date in enumerate(dates):
                try:
                    date.fromisoformat(filing_date)
                except (ValueError, TypeError):
                    raise ProviderError("sec_date_invalid") from None
                if not isinstance(forms[i], str) or not forms[i].strip():
                    raise ProviderError("sec_form_invalid")
                all_dates.append(filing_date)
                if filing_date >= cutoff:
                    recent_rows.append({key: (values[i] if i < len(values) else "") for key, values in table.items() if isinstance(values, list)})
        if not all_dates or min(all_dates) > cutoff:
            return {**unknown, "source_url": source_url, "flags": ["SEC_365_DAY_COVERAGE_UNPROVEN"]}
        latest_date = max(all_dates)
        flags = set()
        # A date without an acceptance timestamp covers the full NEW YORK
        # filing day. Every latest-day record must have a valid acceptance
        # timestamp before that conservative day-end bound can be relaxed.
        latest_time = datetime.combine(date.fromisoformat(latest_date) + timedelta(days=1), time.min, NY)
        latest_acceptance = []
        latest_acceptance_complete = True
        for row in recent_rows:
            form = row.get("form", "").strip().upper()
            if form in REGISTRATION_FORMS or form.startswith("424B"):
                flags.add("REGISTRATION_OR_OFFERING:" + form)
            if form in {"8-K", "8-K/A", "6-K", "6-K/A"}:
                items = str(row.get("items", ""))
                if any(item in items for item in ("1.01", "2.03", "3.02")):
                    flags.add("FINANCING_RELATED_ITEM_REQUIRES_REVIEW:" + form)
            description = str(row.get("primaryDocDescription", "")).lower()
            if re.search(r"\batm\b|at.the.market|diluti|financing|securities purchase|registered direct|equity offering", description):
                flags.add("FINANCING_METADATA_REQUIRES_REVIEW")
            if row.get("filingDate") == latest_date:
                if not row.get("acceptanceDateTime"):
                    latest_acceptance_complete = False
                else:
                    accepted = _dt(row["acceptanceDateTime"])
                    if accepted.astimezone(NY).date().isoformat() != latest_date:
                        latest_acceptance_complete = False
                    latest_acceptance.append(accepted)
        if latest_acceptance and latest_acceptance_complete:
            latest_time = max(latest_acceptance)
        result = {"status": "risk" if flags else "unknown", "flags": sorted(flags),
                  "checked_at": _iso(now), "source_url": source_url, "coverage_days": 365,
                  "latest_filing_date": latest_date, "limitation": "Metadata cannot rule out ATM/dilution; whole-document review required."}
        if flags:
            return result
        review = entry.get("filings_review") or {}
        if review.get("status") != "clear" or review.get("latest_filing_date") != latest_date:
            return result
        try:
            checked = _dt(review.get("checked_at"))
            coverage = _num(review.get("coverage_days"))
        except ProviderError:
            return result
        review_url = str(review.get("source_url", ""))
        parsed = urlsplit(review_url)
        if (not latest_time <= checked <= now or coverage < 365 or not review.get("reviewed_by") or
                parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"} or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            return result
        result.update(status="clear", checked_at=_iso(checked), source_url=review_url,
                      reviewed_by=review["reviewed_by"], coverage_days=coverage)
        return result

    def collect(self, context: dict, now: datetime | None = None):
        # Tests may supply a fixed clock. Real collections always validate
        # against the current wall clock, not the scan-start timestamp.
        fixed_now = now
        current_time = lambda: fixed_now if fixed_now is not None else datetime.now(UTC)
        self._deadline = elapsed_time.monotonic() + COLLECTION_BUDGET_SECONDS
        started = current_time()
        bundle = {"mode": "live", "as_of": _iso(started), "quote_source": {"provider": "alpaca", "feed": "sip", "realtime": False},
                  "market": {}, "securities": [], "errors": [], "scope": "explicit_verified_universe_max_20"}
        if not isinstance(context, dict):
            bundle["errors"].append("context_invalid")
            return bundle
        for key in ("fx", "account", "costs"):
            bundle[key] = context.get(key, {})
        key = self.env.get("ALPACA_API_KEY_ID", "")
        secret = self.env.get("ALPACA_API_SECRET_KEY", "")
        if not key or not secret:
            bundle["errors"].append("alpaca_credentials_missing")
        if self.env.get("ALPACA_SIP_CONFIRMED") != "yes":
            bundle["errors"].append("real_time_sip_entitlement_unconfirmed")
        entries = context.get("symbols", [])
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SYMBOLS:
            bundle["errors"].append("verified_universe_required_max_20")
        elif any(not isinstance(e, dict) or not SYMBOL_RE.fullmatch(str(e.get("symbol", ""))) for e in entries):
            bundle["errors"].append("universe_symbol_invalid")
        elif len({e["symbol"] for e in entries}) != len(entries):
            bundle["errors"].append("universe_duplicate_symbol")
        if bundle["errors"]:
            return bundle
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}

        def validate_clock(clock, calendar):
            stamp = _dt(clock.get("timestamp"))
            actual_now = current_time()
            if abs((actual_now - stamp).total_seconds()) > 30 or not isinstance(clock.get("is_open"), bool):
                raise ProviderError("official_market_clock_invalid")
            session_date = stamp.astimezone(NY).date().isoformat()
            session = next((d for d in calendar if d.get("date") == session_date), None)
            if clock["is_open"] and (not session or not _session(session, "open") <= stamp.astimezone(NY) < _session(session, "close")):
                raise ProviderError("clock_calendar_disagreement")
            bundle["market"] = {"is_open": clock["is_open"], "as_of": _iso(stamp), "session_date": session_date,
                                "source": "alpaca_paper_clock_calendar"}
            if not clock["is_open"]:
                raise ProviderError("regular_market_closed")
            return session_date

        try:
            clock = self._get(PAPER + "/v2/clock", headers)
            calendar = self._get(PAPER + "/v2/calendar", headers,
                                 {"start": (started - timedelta(days=45)).date().isoformat(), "end": started.date().isoformat()})
            _validate_calendar(calendar)
            validate_clock(clock, calendar)
            assets = self._get(PAPER + "/v2/assets", headers, {"status": "active", "asset_class": "us_equity"})
            if not isinstance(assets, list):
                raise ProviderError("assets_schema_invalid")
            assets = {a.get("symbol"): a for a in assets if isinstance(a, dict)}
        except ProviderError as exc:
            bundle["errors"].append(str(exc))
            return bundle
        except Exception:
            bundle["errors"].append("market_schema_invalid")
            return bundle
        prepared = []
        for entry in entries:
            symbol = entry["symbol"]
            security = {"symbol": symbol}
            try:
                asset = assets.get(symbol, {})
                exchange = asset.get("exchange")
                if asset.get("status") != "active" or asset.get("class") != "us_equity" or exchange not in {"NASDAQ", "NYSE", "AMEX"}:
                    raise ProviderError("eligible_listed_asset_unverified")
                if entry.get("exchange") != exchange or entry.get("kind") != "stock":
                    raise ProviderError("common_stock_context_unverified")
                security.update(exchange=exchange, kind="stock", broker_available=entry.get("broker_available") is True,
                                broker_verified_at=entry.get("broker_verified_at"))
                bars = self._bars(symbol, headers, current_time())
                security["news"] = self._news(entry, headers, current_time())
                security["filings"] = self._filings(entry, current_time())
                prepared.append((security, bars))
            except ProviderError as exc:
                bundle["errors"].append(symbol + ":" + str(exc))
                security["provider_error"] = str(exc)
                if str(exc) == "collection_budget_exceeded":
                    bundle["securities"].append(security)
                    return bundle
            except Exception:
                bundle["errors"].append(symbol + ":provider_schema_invalid")
                security["provider_error"] = "provider_schema_invalid"
            bundle["securities"].append(security)
        if not prepared:
            return bundle
        # Slow history/filing/news requests are complete. Quotes are fetched in
        # one final batch, followed only by the official clock refresh. No
        # per-symbol HTTP calls take place after the snapshot request.
        try:
            snapshots = self._get(DATA + "/v2/stocks/snapshots", headers,
                                  {"symbols": ",".join(s["symbol"] for s, _ in prepared), "feed": "sip"})
            if not isinstance(snapshots, dict):
                raise ProviderError("snapshots_schema_invalid")
            session_date = validate_clock(self._get(PAPER + "/v2/clock", headers), calendar)
            bundle["quote_source"]["realtime"] = True
            bundle["as_of"] = _iso(current_time())
        except ProviderError as exc:
            bundle["errors"].append(str(exc))
            return bundle
        except Exception:
            bundle["errors"].append("market_schema_invalid")
            return bundle
        for security, bars in prepared:
            symbol = security["symbol"]
            try:
                _remaining_timeout(self._deadline)
                actual_now = current_time()
                snapshot = snapshots.get(symbol, {})
                quote, trade = snapshot.get("latestQuote", {}), snapshot.get("latestTrade", {})
                quote_time, trade_time = _dt(quote.get("t")), _dt(trade.get("t"))
                if quote_time < datetime(2025, 11, 3, tzinfo=UTC):
                    raise ProviderError("legacy_quote_size_units_unverified")
                if not 0 <= (actual_now - quote_time).total_seconds() <= 15 or not 0 <= (actual_now - trade_time).total_seconds() <= 30:
                    raise ProviderError("quote_or_trade_stale")
                # Both CTA CQS and UTP use R for regular, two-sided automated
                # quotations. Fresh timestamps alone do not exclude nonfirm,
                # closed or otherwise ineligible quote conditions. Unknown or
                # mixed conditions require explicit support before acceptance.
                if quote.get("c") != ["R"]:
                    raise ProviderError("regular_quote_condition_unverified")
                security["quote"] = {"bid": _num(quote.get("bp"), positive=True), "ask": _num(quote.get("ap"), positive=True),
                                     "bid_size": _num(quote.get("bs")), "ask_size": _num(quote.get("as")),
                                     "as_of": _iso(quote_time), "conditions": ["R"],
                                     "size_interpretation": "shares_since_2025_11_03"}
                security["trade"] = {"price": _num(trade.get("p"), positive=True), "as_of": _iso(trade_time)}
                previous = snapshot.get("prevDailyBar", {})
                previous_time = _dt(previous.get("t"))
                previous_session = max((d["date"] for d in calendar if d["date"] < session_date), default=None)
                if (not timedelta(0) < actual_now - previous_time <= timedelta(days=7) or
                        previous_time.astimezone(NY).date().isoformat() != previous_session):
                    raise ProviderError("previous_close_stale")
                daily_time = _dt(snapshot.get("dailyBar", {}).get("t"))
                if daily_time.astimezone(NY).date().isoformat() != session_date:
                    raise ProviderError("current_daily_snapshot_session_invalid")
                adjusted_close = _previous_adjusted_close(bars, calendar, actual_now)
                snapshot_close = _num(previous.get("c"), positive=True)
                # The final regular-session bar may differ slightly from an
                # official auction close. Larger mismatches are ambiguous
                # corporate actions / inconsistent sources and force PAS.
                if abs(snapshot_close / adjusted_close - 1) > 0.001:
                    raise ProviderError("previous_close_adjustment_ambiguous")
                security["metrics"] = _metrics(bars, calendar, actual_now, security["trade"]["price"], adjusted_close)
                security["metrics"]["previous_close_basis"] = "split_adjusted_prior_regular_session_final_5min_close"
                _remaining_timeout(self._deadline)
            except ProviderError as exc:
                bundle["errors"].append(symbol + ":" + str(exc))
                security["provider_error"] = str(exc)
            except Exception:
                bundle["errors"].append(symbol + ":provider_schema_invalid")
                security["provider_error"] = "provider_schema_invalid"
        return bundle


def collect(context: dict, now: datetime | None = None):
    """Collect available evidence; missing subscriptions/inputs return errors.

    Context is local review/configuration, never a source of live quote values.
    No credentials, submitted orders, or executable actions are returned.
    """
    return ReadOnlyProvider().collect(context, now)
