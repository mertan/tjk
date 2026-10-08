"""Local, permission-attested export ranking for bounded research collection.

No network, filesystem, credentials, executable quotes, or order operations.
An operator's permission/source attestations are validated, not independently
proved. Ranking says which observations to review first, never what to buy.

Eligible positive movers receive an equal-weight score from their session-share
volume and price-change percentiles. Each percentile is the count of strictly
smaller values divided by (eligible_count - 1); equal values have equal ranks.
Score ties use descending change, descending volume, then ascending symbol.
All observations must share one declared volume coverage/basis and a snapshot
window of at most 60 seconds. Percentiles are within this supplied export, not
market-wide relative volume or an assurance of strong absolute liquidity.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .engine import MAX_PRICE_USD, MIN_PRICE_USD

MAX_RECORDS = 10000
MAX_SELECTED = 20
MAX_ROW_AGE_SECONDS = 900
MAX_RETRIEVAL_AGE_SECONDS = 300
MAX_SNAPSHOT_SKEW_SECONDS = 60
_CONTEXT = Context(prec=512)
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
_SOURCE_FIELDS = frozenset({"provider", "url", "terms_url", "permission",
    "permission_reviewed_at", "verification", "retrieved_at", "delay_seconds",
    "volume_scope", "volume_basis"})
_ROW_FIELDS = frozenset({"symbol", "exchange", "price_usd", "previous_close_usd",
    "volume_shares", "as_of", "session_date"})
_BLOCKED_DOMAINS = ("tradingview.com", "nasdaq.com", "stooq.com", "stooq.pl")
_NY = ZoneInfo("America/New_York")


def _result():
    return {"status": "unavailable", "selected_symbols": [], "research_priority": [],
            "rejected": [], "source": None, "errors": [], "input_count": 0,
            "eligible_count": 0}


def _timestamp(value, now, max_age):
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        return None, "INVALID_AWARE_TIMESTAMP"
    try:
        instant = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None, "INVALID_AWARE_TIMESTAMP"
        instant = instant.astimezone(timezone.utc)
        age = (now - instant).total_seconds()
        if age < 0:
            return None, "FUTURE_TIMESTAMP"
        if age > max_age:
            return None, "STALE_TIMESTAMP"
        return instant, None
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID_AWARE_TIMESTAMP"


def _url(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048 or
            any(ord(ch) < 33 or ord(ch) == 127 for ch in value) or "\\" in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                "?" in value or "#" in value or parsed.port not in (None, 443)):
            return None
        host = parsed.hostname.lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?", host):
            return None
        return host
    except (ValueError, TypeError):
        return None


def _domain(host, domain):
    return host == domain or host.endswith("." + domain)


def _source(value, now):
    if not isinstance(value, dict) or set(value) != _SOURCE_FIELDS:
        return None, "RANKING_SOURCE_SCHEMA_INVALID"
    provider = value["provider"]
    if provider not in ("finviz_elite_export", "licensed_public_export"):
        return None, "RANKING_PROVIDER_NOT_PERMITTED"
    host, terms_host = _url(value["url"]), _url(value["terms_url"])
    if host is None or terms_host is None:
        return None, "RANKING_SOURCE_URL_INVALID"
    if any(_domain(host, item) or _domain(terms_host, item) for item in _BLOCKED_DOMAINS):
        return None, "RANKING_PROVIDER_NOT_PERMITTED"
    if provider == "finviz_elite_export":
        if host not in ("finviz.com", "www.finviz.com", "elite.finviz.com") or not _domain(terms_host, "finviz.com"):
            return None, "RANKING_FINVIZ_ELITE_EXPORT_REQUIRED"
    elif _domain(host, "finviz.com") or _domain(terms_host, "finviz.com"):
        return None, "RANKING_FINVIZ_ELITE_EXPORT_REQUIRED"
    if (value["permission"] != "personal_automated_analysis" or
            value["verification"] != "operator_reviewed_original_export"):
        return None, "RANKING_PERMISSION_ATTESTATION_REQUIRED"
    reviewed, error = _timestamp(value["permission_reviewed_at"], now, 30 * 86400)
    if error:
        return None, "RANKING_PERMISSION_" + error
    retrieved, error = _timestamp(value["retrieved_at"], now, MAX_RETRIEVAL_AGE_SECONDS)
    if error:
        return None, "RANKING_RETRIEVAL_" + error
    delay = value["delay_seconds"]
    if type(delay) is not int or not 0 <= delay <= MAX_ROW_AGE_SECONDS:
        return None, "RANKING_DELAY_UNKNOWN_OR_UNSUPPORTED"
    if (value["volume_scope"] not in ("consolidated_us", "iex") or
            value["volume_basis"] != "session_cumulative_shares"):
        return None, "RANKING_VOLUME_BASIS_UNSUPPORTED"
    report = {key: value[key] for key in _SOURCE_FIELDS}
    report.update(assurance="OPERATOR_ATTESTATION_NOT_INDEPENDENTLY_VERIFIED",
                  retrieval_age_seconds=(now - retrieved).total_seconds(),
                  declared_delayed=delay > 0, executable_nbbo=False,
                  ranking_scope="SUPPLIED_EXPORT_ONLY", execution_enabled=False)
    return (report, retrieved), None


def _number(value, *, maximum=Decimal("1e12"), integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        raw = str(value)
        if len(raw) > 100:
            return None
        number = Decimal(raw)
        if (not number.is_finite() or number <= 0 or number > maximum or
                number.adjusted() < -12 or
                integer and number != number.to_integral_value()):
            return None
        return number
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _rejected(index, symbol, *errors):
    return {"index": index, "symbol": symbol if isinstance(symbol, str) and
            _SYMBOL.fullmatch(symbol) else None, "errors": list(errors), "decision": "PAS"}


def select(ranking_input, listed, now):
    """Select at most 20 NASDAQ/NYSE symbols from an authorized local export.

    ``listed`` is the previously validated exchange-directory symbol mapping.
    No alphabetical, stale-data, or network fallback is performed. Malformed
    envelopes or duplicate/conflicting symbols invalidate the entire export;
    unsupported/out-of-range/stale individual observations are rejected.
    """
    with localcontext(_CONTEXT):
        return _select(ranking_input, listed, now)


def _select(ranking_input, listed, now):
    result = _result()
    if ranking_input is None:
        result["errors"] = ["RANKING_INPUT_REQUIRED"]
        return result
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
            not isinstance(listed, dict)):
        result["errors"] = ["RANKING_CONTEXT_INVALID"]
        return result
    now = now.astimezone(timezone.utc)
    if (not isinstance(ranking_input, dict) or
            set(ranking_input) != {"schema_version", "source", "records"} or
            type(ranking_input["schema_version"]) is not int or ranking_input["schema_version"] != 1):
        result["errors"] = ["RANKING_SCHEMA_INVALID"]
        return result
    records = ranking_input["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        result["errors"] = ["RANKING_RECORD_LIMIT_OR_SCHEMA_INVALID"]
        return result
    result["input_count"] = len(records)
    source, error = _source(ranking_input["source"], now)
    if error:
        result["errors"] = [error]
        return result
    result["source"], retrieved = source
    seen = set()
    for row in records:
        if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
            result["errors"] = ["RANKING_ROW_SCHEMA_INVALID"]
            return result
        symbol = row["symbol"]
        if isinstance(symbol, str):
            if symbol in seen:
                result["errors"] = ["RANKING_DUPLICATE_OR_CONFLICTING_SYMBOL"]
                return result
            seen.add(symbol)
    session = now.astimezone(_NY).date()
    eligible = []
    for index, row in enumerate(records):
        symbol = row["symbol"]
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            result["rejected"].append(_rejected(index, symbol, "INVALID_SYMBOL"))
            continue
        listing = listed.get(symbol)
        if (row["exchange"] not in ("NASDAQ", "NYSE") or not isinstance(listing, dict) or
                listing.get("exchange") != row["exchange"]):
            result["rejected"].append(_rejected(index, symbol, "LISTING_OR_EXCHANGE_UNVERIFIED"))
            continue
        instant, error = _timestamp(row["as_of"], now, MAX_ROW_AGE_SECONDS)
        if error:
            result["rejected"].append(_rejected(index, symbol, error))
            continue
        try:
            day_raw = row["session_date"]
            if not isinstance(day_raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_raw):
                raise ValueError
            day = date.fromisoformat(day_raw)
        except ValueError:
            result["rejected"].append(_rejected(index, symbol, "INVALID_SESSION_DATE"))
            continue
        if day != session or instant.astimezone(_NY).date() != session:
            result["rejected"].append(_rejected(index, symbol, "NOT_CURRENT_NEW_YORK_SESSION"))
            continue
        if instant > retrieved:
            result["rejected"].append(_rejected(index, symbol, "DATA_AFTER_RETRIEVAL"))
            continue
        price = _number(row["price_usd"])
        previous = _number(row["previous_close_usd"], maximum=Decimal("1e6"))
        volume = _number(row["volume_shares"], integer=True)
        if price is None or previous is None or volume is None:
            result["rejected"].append(_rejected(index, symbol, "INVALID_PRICE_OR_VOLUME"))
            continue
        if not MIN_PRICE_USD <= price <= MAX_PRICE_USD:
            result["rejected"].append(_rejected(index, symbol, "PRICE_OUTSIDE_1_TO_5_USD"))
            continue
        change = (price - previous) / previous * 100
        if change <= 0:
            result["rejected"].append(_rejected(index, symbol, "POSITIVE_PRICE_MOVEMENT_REQUIRED"))
            continue
        eligible.append({"symbol": symbol, "exchange": row["exchange"],
                         "price": price, "previous": previous, "volume": volume,
                         "change": change, "instant": instant})
    result["eligible_count"] = len(eligible)
    if not eligible:
        result["errors"] = ["RANKING_NO_ELIGIBLE_OBSERVATIONS"]
        return result
    if (max(row["instant"] for row in eligible) - min(row["instant"] for row in eligible)).total_seconds() > MAX_SNAPSHOT_SKEW_SECONDS:
        result["errors"] = ["RANKING_SNAPSHOT_INCOMPARABLE"]
        return result
    volumes = sorted(row["volume"] for row in eligible)
    changes = sorted(row["change"] for row in eligible)
    divisor = Decimal(max(1, len(eligible) - 1))
    for row in eligible:
        row["volume_rank"] = bisect_left(volumes, row["volume"])
        row["change_rank"] = bisect_left(changes, row["change"])
        row["score"] = row["volume_rank"] + row["change_rank"]
    eligible.sort(key=lambda row: (-row["score"], -row["change"], -row["volume"], row["symbol"]))
    for priority, row in enumerate(eligible[:MAX_SELECTED], start=1):
        age = (now - row["instant"]).total_seconds()
        result["research_priority"].append({
            "priority": priority, "symbol": row["symbol"], "exchange": row["exchange"],
            "price_usd": format(row["price"], "f"), "previous_close_usd": format(row["previous"], "f"),
            "volume_shares": int(row["volume"]), "day_change_pct": format(row["change"], "f"),
            "volume_percentile": format(Decimal(row["volume_rank"]) / divisor * 100, "f"),
            "change_percentile": format(Decimal(row["change_rank"]) / divisor * 100, "f"),
            "rank_score": format(Decimal(row["score"]) / divisor * 50, "f"),
            "as_of": row["instant"].isoformat(), "session_date": session.isoformat(),
            "data_age_seconds": age, "declared_delay_seconds": result["source"]["delay_seconds"],
            # A zero declared feed delay does not make an aged export live.
            "delayed": result["source"]["delay_seconds"] > 0 or age > 30,
            "age_exceeds_live_trade_limit": age > 30,
            "volume_scope": result["source"]["volume_scope"],
            "decision": "PAS", "purpose": "RESEARCH_COLLECTION_PRIORITY_ONLY",
            "execution_enabled": False, "executable_nbbo": False,
            "source_verification": "OPERATOR_ATTESTATION_NOT_INDEPENDENTLY_VERIFIED",
        })
    result["selected_symbols"] = [row["symbol"] for row in result["research_priority"]]
    result["status"] = "available"
    return result
