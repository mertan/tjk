"""No-key, observation-only screening; never an order or AL/HAZIRLIK signal.

Public symbol/SEC downloads are separate from market observations. No website
price scraper is enabled. Local observations require a reviewed, lawful export;
their origin remains an operator attestation, not authenticated exchange data.
The original PR #2 engine is called unchanged, without impersonating Alpaca SIP.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import re
from urllib.parse import urlsplit

from . import engine

UTC = timezone.utc
MAX_OBSERVATIONS = 10000
MAX_SEC_REVIEWS = 20
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
PROVENANCE_KEYS = {
    "provider", "source_url", "terms_url", "permission", "permission_reviewed_at",
    "verification", "as_of", "retrieved_at", "delay_seconds",
}
SECURITY_KEYS = {
    "symbol", "exchange", "kind", "broker_available", "broker_verified_at",
    "trade", "quote", "metrics", "news", "filings_review",
}
BLOCKED_DOMAINS = ("tradingview.com", "nasdaq.com", "stooq.com", "stooq.pl")
SOURCE_GATE = "quote_source:VERIFIED_REALTIME_ALPACA_SIP_REQUIRED"


def _now():
    return datetime.now(UTC)


def _stamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _keys(value, allowed):
    return isinstance(value, dict) and not (set(value) - allowed)


def _url(value):
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() for c in value):
        return False
    try:
        url = urlsplit(value)
        return bool(url.scheme == "https" and url.hostname and "." in url.hostname
                    and not (url.username or url.password or url.query or url.fragment)
                    and url.port in (None, 443))
    except ValueError:
        return False


def _age(value, now, maximum):
    try:
        if not isinstance(value, str):
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        age = (now - parsed).total_seconds()
        return age if 0 <= age <= maximum else None
    except (ValueError, OverflowError, TypeError):
        return None


def _time_info(value, now):
    """Report a parseable original timestamp even when it is too old to use."""
    try:
        if not isinstance(value, str) or len(value) > 64:
            return None, None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, None
        return _stamp(parsed), (now - parsed).total_seconds()
    except (ValueError, OverflowError, TypeError):
        return None, None


def _provenance(value, now):
    errors = []
    if not _keys(value, PROVENANCE_KEYS) or set(value) != PROVENANCE_KEYS:
        return ["PUBLIC_PROVENANCE_REQUIRED"], None
    if value["provider"] not in {"finviz_elite_export", "licensed_public_export"}:
        errors.append("PUBLIC_PROVIDER_NOT_APPROVED")
    for key in ("source_url", "terms_url"):
        if not _url(value[key]):
            errors.append("PUBLIC_PROVENANCE_URL_INVALID")
        else:
            host = urlsplit(value[key]).hostname.lower()
            if any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS):
                errors.append("PUBLIC_SOURCE_USE_NOT_PERMITTED")
            finviz = host == "finviz.com" or host.endswith(".finviz.com")
            if ((value["provider"] == "finviz_elite_export" and not finviz) or
                    (finviz and value["provider"] != "finviz_elite_export")):
                errors.append("PUBLIC_PROVIDER_SOURCE_MISMATCH")
    if value["permission"] != "personal_automated_analysis":
        errors.append("PUBLIC_USE_PERMISSION_UNVERIFIED")
    if value["verification"] != "operator_reviewed_original_export":
        errors.append("PUBLIC_ORIGIN_UNVERIFIED")
    if _age(value["permission_reviewed_at"], now, 30 * 86400) is None:
        errors.append("PUBLIC_PERMISSION_REVIEW_STALE_OR_INVALID")
    age = _age(value["as_of"], now, 30)
    retrieved_age = _age(value["retrieved_at"], now, 60)
    if age is None or retrieved_age is None:
        errors.append("PUBLIC_MARKET_DATA_STALE_OR_TIMESTAMP_UNVERIFIED")
    elif retrieved_age > age:
        errors.append("PUBLIC_RETRIEVAL_PRECEDES_OBSERVATION")
    if type(value["delay_seconds"]) is not int or value["delay_seconds"] != 0:
        errors.append("PUBLIC_DELAYED_OR_UNKNOWN_DATA")
    # A declaration is never described as independent source authentication.
    source_time, report_age = _time_info(value["as_of"], now)
    retrieved_time, _ = _time_info(value["retrieved_at"], now)
    report = {
        "provider": value["provider"] if value["provider"] in {"finviz_elite_export", "licensed_public_export"} else "unapproved",
        "url": value["source_url"] if _url(value["source_url"]) else None,
        "terms_url": value["terms_url"] if _url(value["terms_url"]) else None,
        "source_timestamp": source_time,
        "retrieved_at": retrieved_time, "age_seconds": report_age,
        "delay_seconds": value["delay_seconds"] if type(value["delay_seconds"]) is int and 0 <= value["delay_seconds"] <= 86400 else None,
        "accepted": not errors, "rejection_codes": list(dict.fromkeys(errors)),
        "trust_basis": "OPERATOR_REVIEWED_EXPORT_NOT_INDEPENDENTLY_AUTHENTICATED",
    }
    return errors, report


def _empty(now):
    return {
        "mode": "public_observation_only", "decision": "PAS",
        "data_status": "DATA_UNAVAILABLE", "execution_enabled": False,
        "evaluated_at": _stamp(now), "candidates": [], "watchlist": [],
        "reasons": [], "securities": [], "sources": [],
        "quote_status": "VERIFIED_EXECUTABLE_NBBO_UNAVAILABLE",
        "risk_limits": {
            "capital_try": str(engine.CAPITAL_TRY),
            "position_cap_try": str(engine.POSITION_CAP_TRY),
            "planned_stop_pct": str(engine.PLANNED_STOP_PCT),
            "daily_loss_limit_try": str(engine.DAILY_LOSS_LIMIT_TRY),
            "min_price_usd": str(engine.MIN_PRICE_USD),
            "max_price_usd": str(engine.MAX_PRICE_USD),
            "max_spread_usd": str(engine.MAX_SPREAD_USD),
            "max_spread_pct": str(engine.MAX_SPREAD_RATIO * 100),
        },
        "coverage": {"exchanges": ["NASDAQ", "NYSE"], "universe_count": 0,
                     "observation_count": 0, "sec_review_count": 0,
                     "unobserved_count": 0, "complete_market_scan": False},
    }


def _quote_errors(quote, now):
    """Missing/old quotes cannot authorize trading; malformed/wide quotes block.

    Even perfectly shaped imported quotes are not authenticated executable NBBO.
    Missing quotes may support WATCH; supplied stale quotes fail closed.
    """
    if quote is None:
        return [], {"status": "MISSING", "as_of": None, "age_seconds": None}
    if not _keys(quote, {"bid", "ask", "bid_size", "ask_size", "as_of"}):
        return ["PUBLIC_QUOTE_INVALID"], {"status": "INVALID"}
    errors = []
    bid = engine._number(quote, "bid", "quote", errors, positive=True)
    ask = engine._number(quote, "ask", "quote", errors, positive=True)
    for field in ("bid_size", "ask_size"):
        engine._number(quote, field, "quote", errors, positive=True, integer=True)
    if errors:
        return ["PUBLIC_QUOTE_INVALID"], {"status": "INVALID"}
    with localcontext(engine._CALCULATION_CONTEXT):
        spread = ask - bid
        if bid > ask:
            errors.append("PUBLIC_CROSSED_QUOTE")
        if spread > engine.MAX_SPREAD_USD or spread > bid * engine.MAX_SPREAD_RATIO:
            errors.append("PUBLIC_SPREAD_LIMIT_EXCEEDED")
        if not engine.MIN_PRICE_USD <= ask <= engine.MAX_PRICE_USD:
            errors.append("PUBLIC_ASK_OUTSIDE_PRICE_RANGE")
    age = _age(quote.get("as_of"), now, 15)
    if age is None:
        errors.append("PUBLIC_QUOTE_STALE_OR_TIMESTAMP_UNVERIFIED")
    as_of, report_age = _time_info(quote.get("as_of"), now)
    return errors, {"status": "UNAUTHENTICATED" if age is not None else "STALE_OR_UNTIMED",
                    "as_of": as_of, "age_seconds": report_age, "spread_usd": str(spread)}


def _screen(security, market, context, filings, now, *, prefilter=False):
    """Use the existing engine for all nonquote evidence/account validations.

    The SIP gate intentionally remains failed. No original engine candidate,
    quantity, stop price or other trade draft is exposed from this adapter.
    """
    errors = []
    quote_errors, quote_report = _quote_errors(security.get("quote"), now)
    errors.extend(quote_errors)
    news = security.get("news")
    if not isinstance(news, dict) or news.get("sentiment") != "positive":
        errors.append("PUBLIC_POSITIVE_CATALYST_UNVERIFIED")
    if (not isinstance(news, dict) or news.get("symbol") != security.get("symbol") or
            news.get("original_source_reviewed") is not True):
        errors.append("PUBLIC_NEWS_SOURCE_OR_SYMBOL_UNVERIFIED")
    if not isinstance(news, dict) or not _url(news.get("url")):
        errors.append("PUBLIC_NEWS_PUBLIC_SOURCE_URL_REQUIRED")
    else:
        host = urlsplit(news["url"]).hostname.lower()
        if any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS):
            errors.append("PUBLIC_NEWS_SOURCE_USE_NOT_PERMITTED")
    item = {k: deepcopy(v) for k, v in security.items() if k != "filings_review"}
    item["filings"] = filings
    bundle = {"mode": "live", "errors": [],
              "quote_source": {"provider": "public_export", "feed": "unverified", "realtime": False},
              "market": market, "securities": [item], **context}
    result = engine.evaluate(bundle, now)
    errors.extend(r for r in result["reasons"] if r not in {SOURCE_GATE, "NO_ELIGIBLE_SECURITY"})
    diagnostics = result.get("securities", [])
    if len(diagnostics) != 1:
        errors.append("PUBLIC_RISK_VALIDATION_FAILED")
    else:
        errors.extend(r for r in diagnostics[0]["reasons"]
                      if not r.startswith("securities[0].quote")
                      and not (prefilter and r.startswith("securities[0].filings"))
                      and r != "GLOBAL_DATA_OR_RISK_CHECK_FAILED")
    # The original engine cannot size without an authenticated quote. Check
    # existing aggregate commitments too; never substitute last trade for ask.
    if not errors:
        with localcontext(engine._CALCULATION_CONTEXT):
            account = context["account"]
            positions = account["positions"]
            total = sum((Decimal(str(p["market_value_try"])) for p in positions), Decimal(0))
            held = sum((Decimal(str(p["market_value_try"])) for p in positions
                        if p["symbol"] == security["symbol"]), Decimal(0))
            entry_fee = Decimal(str(context["costs"]["entry_fee_try"]))
            if total + entry_fee >= engine.CAPITAL_TRY or held + entry_fee >= engine.POSITION_CAP_TRY:
                errors.append("PUBLIC_EXISTING_CAPITAL_OR_POSITION_BUDGET_EXHAUSTED")
            fees = Decimal(str(context["costs"]["entry_fee_try"])) + Decimal(str(context["costs"]["exit_fee_try"]))
            loss = max(Decimal(0), -(Decimal(str(account["realized_pnl_try"])) + Decimal(str(account["unrealized_pnl_try"]))))
            if (loss + Decimal(str(account["open_risk_try"])) + fees >= engine.DAILY_LOSS_LIMIT_TRY or
                    Decimal(str(account["cash_try"])) <= Decimal(str(account["reserved_try"])) + fees):
                errors.append("PUBLIC_CASH_OR_DAILY_RISK_BUDGET_EXHAUSTED")
    return list(dict.fromkeys(errors)), quote_report


def _scan(observations, context, environ, instant, sources, live_clock):
    result = _empty(instant)
    if sources is None:
        from .public_sources import PublicSources
        sources = PublicSources(environ=environ, now=None if live_clock else instant)
    universe = sources.universe()
    result["sources"].extend(universe.get("sources", []))
    result["reasons"].extend(universe.get("errors", []))
    entries = universe.get("entries", [])
    listed = {e["symbol"]: e for e in entries}
    result["coverage"]["universe_count"] = len(listed)
    result["coverage"]["unobserved_count"] = len(listed)
    if universe.get("status") != "available":
        result["reasons"].append("PUBLIC_UNIVERSE_UNAVAILABLE")
    if observations is None:
        result["reasons"].append("PERMITTED_CURRENT_MARKET_OBSERVATIONS_UNAVAILABLE")
        return result
    if (not _keys(observations, {"schema_version", "provenance", "market", "securities"}) or
            type(observations.get("schema_version")) is not int or observations["schema_version"] != 1 or
            not isinstance(observations.get("securities"), list) or
            not 1 <= len(observations["securities"]) <= MAX_OBSERVATIONS):
        result["reasons"].append("PUBLIC_OBSERVATION_SCHEMA_INVALID")
        return result
    if not _keys(context, {"fx", "account", "costs"}):
        result["reasons"].append("PUBLIC_CONTEXT_SCHEMA_INVALID")
        return result
    errors, provenance = _provenance(observations.get("provenance"), instant)
    result["reasons"].extend(errors)
    provenance_index = len(result["sources"])
    if provenance:
        result["sources"].append(provenance)
    securities = observations["securities"]
    seen = set()
    for security in securities:
        if (not _keys(security, SECURITY_KEYS) or
                not isinstance(security.get("symbol"), str) or
                not SYMBOL.fullmatch(security["symbol"]) or security["symbol"] in seen):
            result["reasons"].append("PUBLIC_SECURITY_SCHEMA_OR_DUPLICATE_INVALID")
            return result
        seen.add(security["symbol"])
    result["coverage"]["observation_count"] = len(securities)
    result["coverage"]["unobserved_count"] = len(set(listed) - seen)
    if result["reasons"]:
        return result
    # Reject cheap failures before SEC requests. No synthetic filing evidence:
    # the prefilter defers its missing-filings errors, never its other checks.
    shortlist = []
    for security in securities:
        symbol = security["symbol"]
        errors = []
        listing = listed.get(symbol)
        if security.get("exchange") not in {"NASDAQ", "NYSE"}:
            errors.append("PUBLIC_EXCHANGE_OUT_OF_SCOPE")
        if not listing or listing["exchange"] != security.get("exchange"):
            errors.append("PUBLIC_LISTING_NOT_IN_DISCOVERED_UNIVERSE")
        check, quote = _screen(security, observations.get("market"), context, None, instant, prefilter=True)
        errors.extend(check)
        diagnostic = {"symbol": symbol, "decision": "PAS", "reasons": errors,
                      "quote": quote, "filings_status": "NOT_CHECKED"}
        result["securities"].append(diagnostic)
        if not errors:
            shortlist.append((security, diagnostic))
    shortlist.sort(key=lambda pair: tuple(Decimal(str(pair[0]["metrics"][k])) for k in
                   ("rvol_5m", "return_5m_pct", "day_change_pct", "day_dollar_volume")), reverse=True)
    reviewed = []
    if shortlist:
        mapping = sources.symbol_map()
        result["sources"].extend(mapping.get("sources", []))
        result["reasons"].extend(mapping.get("errors", []))
        if mapping.get("status") != "available":
            result["reasons"].append("PUBLIC_SEC_SYMBOL_MAPPING_UNAVAILABLE")
            return result
        for index, (security, diagnostic) in enumerate(shortlist):
            if index >= MAX_SEC_REVIEWS:
                diagnostic["reasons"].append("PUBLIC_SEC_REVIEW_LIMIT_NOT_EVALUATED")
                continue
            symbol = security["symbol"]
            entry = mapping.get("mapping", {}).get(symbol)
            if not entry or entry.get("exchange") != security["exchange"]:
                diagnostic["reasons"].append("PUBLIC_SEC_SYMBOL_EXCHANGE_MISMATCH")
                continue
            filings = sources.filings({**security, "cik": entry["cik"]}, instant)
            result["sources"].extend(filings.get("sources", []))
            result["coverage"]["sec_review_count"] += 1
            diagnostic["filings_status"] = filings.get("status", "unknown")
            diagnostic["filings_flags"] = filings.get("flags", [])
            if filings.get("error"):
                diagnostic["reasons"].append(filings["error"])
            checked_now = _now() if live_clock else instant
            evidence_errors, quote = _screen(security, observations.get("market"), context, filings, checked_now)
            source_errors, _ = _provenance(observations["provenance"], checked_now)
            diagnostic["reasons"].extend(evidence_errors + source_errors)
            diagnostic["quote"] = quote
            reviewed.append((security, diagnostic, filings))
            if not diagnostic["reasons"]:
                diagnostic["decision"] = "İZLE"
                result["watchlist"].append({
                    "symbol": symbol, "exchange": security["exchange"], "decision": "İZLE",
                    "last_price_usd": str(security["trade"]["price"]),
                    "trade_as_of": security["trade"]["as_of"],
                    "metrics_as_of": security["metrics"]["as_of"],
                    "metrics_window_end": security["metrics"]["window_end"],
                    "news_published_at": security["news"]["published_at"],
                    "news_reviewed_at": security["news"]["reviewed_at"],
                    "news_url": security["news"]["url"],
                    "news_category": security["news"]["category"],
                    "filings_checked_at": filings.get("checked_at"),
                    "filings_url": filings.get("source_url") if _url(filings.get("source_url")) else None,
                    "execution_enabled": False,
                    "risk_status": "ACCOUNT_GATES_CHECKED_NO_POSITION_SIZED",
                    "reason": "VERIFIED_EXECUTABLE_NBBO_UNAVAILABLE_NO_TRADE_PLAN",
                })
    if live_clock:
        finished = _now()
        result["evaluated_at"] = _stamp(finished)
        for security, diagnostic, filings in reviewed:
            errors, quote = _screen(security, observations.get("market"), context, filings, finished)
            diagnostic["quote"] = quote
            if errors:
                diagnostic["reasons"] = list(dict.fromkeys(diagnostic["reasons"] + errors))
                diagnostic["decision"] = "PAS"
        still_valid = {d["symbol"] for d in result["securities"] if d["decision"] == "İZLE"}
        result["watchlist"] = [w for w in result["watchlist"] if w["symbol"] in still_valid]
        # An early symbol must not survive a later, slow SEC request.
        final_source_errors, final_provenance = _provenance(observations["provenance"], finished)
        result["sources"][provenance_index] = final_provenance
        if final_source_errors:
            result["watchlist"] = []
            result["reasons"].append("PUBLIC_DATA_EXPIRED_DURING_COLLECTION")
            for diagnostic in result["securities"]:
                diagnostic["decision"] = "PAS"
    if result["watchlist"] and not result["reasons"]:
        result["decision"] = "İZLE"
        result["data_status"] = "OBSERVATIONS_ONLY"
    else:
        result["watchlist"] = []
        result["reasons"].append("NO_VERIFIED_WATCH_CANDIDATE")
    return result


def scan(observations=None, context=None, *, environ=None, now=None, sources=None):
    """One bounded public scan. No manager connection, key read, or file write.

    Local provenance/positive-news reviews are explicit operator attestations;
    the program cannot authenticate them or manufacture missing provider times.
    A watch item is never a sized/approved position, even with imported bid/ask.
    """
    instant = now if now is not None else _now()
    try:
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("invalid clock")
        instant = instant.astimezone(UTC)
        result = _scan(observations, {} if context is None else context, environ, instant, sources, now is None)
        result["completed_at"] = _stamp(_now() if now is None else instant)
        return result
    except Exception:
        # Never expose source bodies, exception messages, contact details or keys.
        result = _empty(_now())
        result["reasons"] = ["PUBLIC_SCAN_FAILED_CLOSED"]
        return result
