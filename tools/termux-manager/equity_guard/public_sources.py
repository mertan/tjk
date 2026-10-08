"""No-key, read-only official directory and SEC sources; never market quotes.

Nasdaq Trader documents daily directory downloads for personal noncommercial
use. SEC documents its public submissions JSON. Neither source supplies a
current executable bid/ask. Their retrieval time is never a quote timestamp.
No TradingView/Finviz page scraping or undocumented endpoints are implemented.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .providers import ProviderError, ReadOnlyProvider, _bounded_body, _iso, _pace_sec

UTC = timezone.utc
NASDAQ_DIRECTORY = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NYSE_DIRECTORY = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_MAPPING = "https://www.sec.gov/files/company_tickers_exchange.json"
DEFAULT_REQUEST_BUDGET = 24
DEFAULT_SECONDS_BUDGET = 60
DIRECTORY_MAX_AGE = timedelta(hours=96)
# Source definitions do not document the trailer's timezone. A conservative
# bound can reject definitely stale/future dates without inventing a timezone.
UNKNOWN_TIMEZONE_BOUND = timedelta(hours=14)
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-$^]{0,14}$")
KNOWN_FILINGS_ERRORS = frozenset({
    "sec_symbol_cik_mismatch", "sec_pagination_limit_reached",
    "sec_archive_path_invalid", "sec_schema_invalid", "sec_date_invalid",
    "sec_form_invalid", "timestamp_missing", "timestamp_invalid",
    "timestamp_timezone_missing", "collection_budget_exceeded",
    "provider_request_failed", "endpoint_rejected",
})


class PublicSourceError(ProviderError):
    """Raised with a fixed diagnostic code; never response or credential text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def allowed_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.port not in (None, 443) or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            return False
        if url in {NASDAQ_DIRECTORY, NYSE_DIRECTORY, SEC_MAPPING}:
            return True
        return parsed.hostname == "data.sec.gov" and bool(re.fullmatch(
            r"/submissions/CIK\d{10}(?:-submissions-\d{3,})?\.json", parsed.path))
    except (TypeError, ValueError):
        return False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicSourceError("redirect_rejected")


class PublicTransport:
    """Bounded GET only; inherit OS proxy and default verified TLS trust.

    No retry is attempted. A 403 or 429 stops this transport for the remainder
    of the collection. Headers/body/exception strings are never reported.
    """

    def __init__(self, *, request_budget=DEFAULT_REQUEST_BUDGET,
                 seconds_budget=DEFAULT_SECONDS_BUDGET, opener=None):
        if (isinstance(request_budget, bool) or not isinstance(request_budget, int) or
                not 1 <= request_budget <= DEFAULT_REQUEST_BUDGET or
                isinstance(seconds_budget, bool) or not isinstance(seconds_budget, (int, float)) or
                not 0 < seconds_budget <= DEFAULT_SECONDS_BUDGET):
            raise PublicSourceError("budget_invalid")
        self.remaining_requests = request_budget
        self.deadline = time.monotonic() + seconds_budget
        self.blocked = False
        self.opener = opener or build_opener(
            _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def get(self, url, headers=None):
        if not allowed_url(url):
            raise PublicSourceError("endpoint_rejected")
        if self.blocked:
            raise PublicSourceError("source_access_stopped")
        if self.remaining_requests <= 0:
            raise PublicSourceError("request_budget_exceeded")
        headers = dict(headers or {})
        if any(not isinstance(k, str) or k.lower() not in {
                "user-agent", "accept", "accept-encoding"} for k in headers):
            raise PublicSourceError("credential_scope_rejected")
        if any(not isinstance(v, str) or "\n" in v or "\r" in v for v in headers.values()):
            raise PublicSourceError("header_invalid")
        if "sec.gov" in urlsplit(url).hostname:
            if not any(k.lower() == "user-agent" and v.strip() for k, v in headers.items()):
                raise PublicSourceError("sec_user_agent_required")
        self.remaining_requests -= 1
        try:
            if urlsplit(url).hostname in {"www.sec.gov", "data.sec.gov"}:
                _pace_sec(self.deadline)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise PublicSourceError("collection_budget_exceeded")
            request = Request(url, headers=headers, method="GET")
            with self.opener.open(request, timeout=min(12, remaining)) as response:
                if response.status != 200:
                    if response.status in {403, 429}:
                        self.blocked = True
                    raise PublicSourceError("http_" + str(response.status))
                encoding = response.headers.get("Content-Encoding", "identity").lower()
                if encoding not in {"", "identity"}:
                    raise PublicSourceError("content_encoding_rejected")
                return _bounded_body(response, self.deadline)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {403, 429}:
                self.blocked = True
            raise PublicSourceError("http_" + str(code)) from None
        except PublicSourceError:
            raise
        except ProviderError as exc:
            code = str(exc)
            raise PublicSourceError(code if code in {
                "collection_budget_exceeded", "response_too_large"} else "source_request_failed") from None
        except (URLError, TimeoutError, OSError):
            raise PublicSourceError("network_unavailable") from None
        except Exception:
            raise PublicSourceError("source_request_failed") from None


def _source(url, now):
    return {"provider": "SEC" if "sec.gov" in url else "Nasdaq Trader",
            "url": url, "status": "available", "retrieved_at": _iso(now), "source_timestamp": None,
            "delay_seconds": None, "freshness_verified": False}


def _failed_source(url, attempted_at, code):
    return {"provider": "SEC" if "sec.gov" in url else "Nasdaq Trader",
            "url": url, "status": "unavailable", "attempted_at": _iso(attempted_at),
            "retrieved_at": None, "source_timestamp": None, "delay_seconds": None,
            "freshness_verified": False, "error": code}


def parse_directory(text, url, now):
    """Parse official Nasdaq/other-listing file; stock kind stays unverified.

    Exchange/type metadata eliminates tests, ETFs and obvious noncommon
    instruments, but a security name is not proof of common-stock status.
    """
    if url not in {NASDAQ_DIRECTORY, NYSE_DIRECTORY}:
        raise PublicSourceError("directory_source_invalid")
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise PublicSourceError("directory_schema_invalid")
    lines = text.strip().splitlines()
    if len(lines) < 3:
        raise PublicSourceError("directory_schema_invalid")
    headers = lines[0].split("|")
    symbol_field = "Symbol" if url == NASDAQ_DIRECTORY else "ACT Symbol"
    required = {symbol_field, "Security Name", "Test Issue", "ETF"}
    required.add("Financial Status" if url == NASDAQ_DIRECTORY else "Exchange")
    if len(set(headers)) != len(headers) or not required.issubset(headers):
        raise PublicSourceError("directory_schema_invalid")
    footer = re.fullmatch(r"File Creation Time: (\d{8}\d{2}:\d{2})\|*", lines[-1])
    if not footer:
        raise PublicSourceError("directory_timestamp_missing")
    try:
        stamp = datetime.strptime(footer.group(1), "%m%d%Y%H:%M")
    except ValueError:
        raise PublicSourceError("directory_timestamp_invalid") from None
    # Do not assign UTC or America/New_York without a source timezone.
    naive_now = now.astimezone(UTC).replace(tzinfo=None)
    if stamp - UNKNOWN_TIMEZONE_BOUND > naive_now:
        raise PublicSourceError("directory_timestamp_future")
    if naive_now - (stamp + UNKNOWN_TIMEZONE_BOUND) > DIRECTORY_MAX_AGE:
        raise PublicSourceError("directory_stale")
    metadata = _source(url, now)
    metadata.update(source_timestamp_raw=footer.group(1), source_timezone=None,
                    timestamp_basis="file_creation_timezone_unspecified",
                    limitation="Directory freshness and common-stock type are not independently verified.")
    entries, seen = [], set()
    for line in lines[1:-1]:
        values = line.split("|")
        if len(values) != len(headers):
            raise PublicSourceError("directory_schema_invalid")
        row = dict(zip(headers, values))
        symbol = row[symbol_field]
        if not SYMBOL.fullmatch(symbol) or symbol in seen:
            raise PublicSourceError("directory_symbol_invalid")
        seen.add(symbol)
        if row["Test Issue"] not in {"Y", "N"} or row["ETF"] not in {"Y", "N"}:
            raise PublicSourceError("directory_flags_invalid")
        if row["Test Issue"] != "N" or row["ETF"] != "N" or row.get("NextShares", "N") != "N":
            continue
        if url == NASDAQ_DIRECTORY:
            if row["Financial Status"] != "N":
                continue
            exchange = "NASDAQ"
        else:
            if row["Exchange"] != "N":
                continue
            exchange = "NYSE"
        name = row["Security Name"]
        # Defensive exclusion only, never a positive security-type inference.
        if (not name or re.search(r"\b(warrants?|rights?|units?|preferred|depositary|notes?|bonds?|fund|etn|etf)\b",
                                  name, re.IGNORECASE) or any(c in symbol for c in "$^")):
            continue
        entries.append({"symbol": symbol, "exchange": exchange,
                        "security_name": name, "kind": "unverified", "source_url": url})
    if not entries:
        raise PublicSourceError("directory_no_eligible_entries")
    return {"entries": entries, "source": metadata}


def parse_sec_mapping(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list) or not isinstance(payload.get("data"), list):
        raise PublicSourceError("sec_mapping_schema_invalid")
    fields = payload["fields"]
    if (any(not isinstance(f, str) for f in fields) or len(set(fields)) != len(fields) or
            not {"cik", "ticker", "exchange"}.issubset(fields)):
        raise PublicSourceError("sec_mapping_schema_invalid")
    result, ambiguous = {}, set()
    for values in payload["data"]:
        if not isinstance(values, list) or len(values) != len(fields):
            raise PublicSourceError("sec_mapping_schema_invalid")
        row = dict(zip(fields, values))
        if row["exchange"] not in {"Nasdaq", "NYSE"}:
            continue
        symbol, cik = row["ticker"], row["cik"]
        if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or isinstance(cik, bool) or
                not isinstance(cik, (int, str)) or not re.fullmatch(r"[0-9]{1,10}", str(cik)) or int(cik) <= 0):
            raise PublicSourceError("sec_mapping_schema_invalid")
        candidate = {"cik": str(cik).zfill(10), "exchange": "NASDAQ" if row["exchange"] == "Nasdaq" else "NYSE"}
        if symbol in result and candidate != result[symbol]:
            ambiguous.add(symbol)
        result[symbol] = candidate
    for symbol in ambiguous:
        result.pop(symbol, None)
    return result


class PublicSources:
    """Official sources; reads only SEC_USER_AGENT, never broker/HMAC secrets."""

    def __init__(self, environ=None, transport=None, now=None):
        env = os.environ if environ is None else environ
        self.sec_user_agent = env.get("SEC_USER_AGENT", "")
        self.transport = transport or PublicTransport()
        self.fixed_now = now
        self.sources = []

    def _now(self):
        return self.fixed_now if self.fixed_now is not None else datetime.now(UTC)

    def _get(self, url):
        agent = self.sec_user_agent if urlsplit(url).hostname in {"www.sec.gov", "data.sec.gov"} else "tjk-public-analysis/1.0"
        if not isinstance(agent, str) or not agent.strip():
            raise PublicSourceError("sec_user_agent_required")
        try:
            raw = self.transport.get(url, {"User-Agent": agent, "Accept": "application/json, text/plain",
                                           "Accept-Encoding": "identity"})
            text = raw.decode("utf-8-sig")
        except PublicSourceError:
            raise
        except UnicodeError:
            raise PublicSourceError("source_encoding_invalid") from None
        except Exception:
            raise PublicSourceError("source_request_failed") from None
        return text

    def universe(self):
        entries, sources, errors = [], [], []
        for url in (NASDAQ_DIRECTORY, NYSE_DIRECTORY):
            attempted = self._now()
            try:
                result = parse_directory(self._get(url), url, self._now())
                result["source"]["attempted_at"] = _iso(attempted)
                entries.extend(result["entries"])
                sources.append(result["source"])
            except PublicSourceError as exc:
                errors.append(str(exc))
                sources.append(_failed_source(url, attempted, str(exc)))
        symbols = [entry["symbol"] for entry in entries]
        if len(set(symbols)) != len(symbols):
            errors.append("directory_cross_exchange_duplicate")
        self.sources.extend(sources)
        return {"status": "unavailable" if errors else "available", "entries": [] if errors else entries,
                "sources": sources, "errors": errors, "scope": "NASDAQ_NYSE_directory_not_verified_stock_types"}

    def symbol_map(self):
        attempted = self._now()
        try:
            payload = json.loads(self._get(SEC_MAPPING))
            entries = parse_sec_mapping(payload)
            source = _source(SEC_MAPPING, self._now())
            source.update(attempted_at=_iso(attempted), timestamp_basis="not_published",
                          limitation="SEC mapping is periodically updated; accuracy and scope are not guaranteed.")
            self.sources.append(source)
            return {"status": "available", "mapping": entries, "sources": [source], "errors": []}
        except PublicSourceError as exc:
            code = str(exc)
        except (ValueError, TypeError):
            code = "sec_mapping_schema_invalid"
        source = _failed_source(SEC_MAPPING, attempted, code)
        self.sources.append(source)
        return {"status": "unavailable", "mapping": {}, "sources": [source], "errors": [code]}

    def filings(self, entry, now=None):
        """Reuse PR #2 risk classification; transport has stricter no-retry policy."""
        source_start = len(self.sources)

        def getter(url, headers, params):
            if params:
                raise ProviderError("endpoint_rejected")
            attempted = self._now()
            try:
                payload = json.loads(self._get(url))
            except PublicSourceError as exc:
                self.sources.append(_failed_source(url, attempted, str(exc)))
                raise
            except (ValueError, TypeError):
                self.sources.append(_failed_source(url, attempted, "sec_schema_invalid"))
                raise ProviderError("sec_schema_invalid") from None
            source = _source(url, self._now())
            source["attempted_at"] = _iso(attempted)
            self.sources.append(source)
            return payload

        provider = ReadOnlyProvider(getter=getter, environ={"SEC_USER_AGENT": self.sec_user_agent})
        try:
            if not isinstance(self.sec_user_agent, str) or not self.sec_user_agent.strip():
                raise PublicSourceError("sec_user_agent_required")
            if not isinstance(entry, dict) or not SYMBOL.fullmatch(str(entry.get("symbol", ""))):
                raise PublicSourceError("sec_entry_invalid")
            if not re.fullmatch(r"[0-9]{1,10}", str(entry.get("cik", ""))):
                raise PublicSourceError("sec_cik_required")
            result = provider._filings(entry, now or self._now())
            # Classification only: a missing review stays unknown and blocks AL.
            return {**result, "sources": self.sources[source_start:]}
        except PublicSourceError as exc:
            code = str(exc)
        except ProviderError as exc:
            code = str(exc) if str(exc) in KNOWN_FILINGS_ERRORS else "sec_lookup_failed"
        except Exception:
            code = "sec_schema_invalid"
        return {"status": "unknown", "flags": [], "coverage_days": 0, "error": code,
                "sources": self.sources[source_start:]}
