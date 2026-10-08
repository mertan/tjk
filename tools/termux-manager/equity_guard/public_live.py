"""Bounded no-key research using the documented Fintable public API.

This feed cannot establish a watch candidate: it has no bid/ask or news, can
be cached, and is not a consolidated tape. Preserve observations for review,
but never manufacture PR #3's provenance, market-open or risk attestations.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo
import os

from . import engine
from .public_adapter import SYMBOL, _empty, _stamp, _time_info

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
MAX_SYMBOLS = 20


def _dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("untimed")
    return parsed


def _metrics(data, feed, now, received_at=None):
    """Describe two consecutive completed regular-hours bars, on one feed.

    A previous five-minute window is not the same-clock multi-session RVOL
    baseline required by the risk engine. It is explicitly labelled here.
    """
    missing = {"status": "DATA_UNAVAILABLE", "return_5m_pct": None,
               "volume_ratio_previous_5m": None, "rvol_5m": None,
               "window_end": None, "window_volume": None,
               "previous_window_volume": None, "feed": feed,
               "basis": "consecutive_completed_5m_bars_not_session_adjusted_RVOL"}
    if not data or data.get("feed") != feed or feed != "iex":
        return {**missing, "reason": "HISTORY_FEED_UNVERIFIED_OR_MISMATCH"}
    try:
        bars = []
        completion_cutoff = min(now, received_at) if received_at is not None else now
        for bar in data["bars"]:
            start = _dt(bar["timestamp"]).astimezone(NY)
            end = start + timedelta(minutes=5)
            if (start.date() == now.astimezone(NY).date() and
                    bar["date"] == start.date().isoformat() and
                    570 <= start.hour * 60 + start.minute <= 955 and
                    start.minute % 5 == 0 and start.second == 0 and
                    start.microsecond == 0 and start.weekday() < 5 and end <= completion_cutoff):
                bars.append((start, end, bar))
        bars.sort(key=lambda item: item[0])
        if len(bars) < 2:
            return {**missing, "reason": "TWO_COMPLETED_REGULAR_SESSION_BARS_REQUIRED"}
        previous, current = bars[-2:]
        if current[0] != previous[1] or not 0 <= (now - current[1]).total_seconds() <= 300:
            return {**missing, "reason": "HISTORY_STALE_OR_NONCONSECUTIVE"}
        with localcontext(engine._CALCULATION_CONTEXT):
            change = (Decimal(current[2]["close"]) / Decimal(previous[2]["close"]) - 1) * 100
            prior_volume = previous[2]["volume"]
            ratio = (Decimal(current[2]["volume"]) / Decimal(prior_volume)
                     if prior_volume > 0 else None)
        return {**missing, "status": "PARTIAL_OBSERVATIONS", "return_5m_pct": str(change),
                "volume_ratio_previous_5m": str(ratio) if ratio is not None else None,
                "window_volume": current[2]["volume"], "previous_window_volume": prior_volume,
                "window_end": _stamp(current[1]),
                "reason": "CONSOLIDATED_SAME_CLOCK_RVOL_UNAVAILABLE"}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return {**missing, "reason": "HISTORY_METRICS_INVALID"}


def collect(*, environ=None, now=None, sources=None, market_source=None, symbols=None,
            ranking_input=None):
    from .fintable import FintableSource
    from .public_sources import PublicSources
    from .research_ranking import select as select_research_symbols

    clock = (lambda: now) if now is not None else (lambda: datetime.now(UTC))
    started = clock()
    result = _empty(started)
    result["mode"] = "public_fintable_research"
    result["attribution"] = {"provider": "Fintable", "url": "https://fintable.io",
                             "terms_url": "https://fintable.io/terms-of-service"}
    result["limitations"] = [
        "PERSONAL_NONCOMMERCIAL_RESEARCH_ONLY",
        "FINTABLE_PRICES_CAN_BE_CACHED_UP_TO_ONE_HOUR_NOT_FOR_TRADING",
        "IEX_VOLUME_IS_NOT_CONSOLIDATED_US_VOLUME",
        "NO_BID_ASK_NEWS_OR_VERIFIED_MARKET_SESSION_FROM_THIS_PROVIDER",
        "SELECTION_COVERS_ONLY_PROVIDED_OBSERVATIONS_NOT_THE_WHOLE_MARKET",
        "RESEARCH_PRIORITY_IS_NOT_A_STOCK_RECOMMENDATION_OR_TRADE_SIGNAL",
    ]
    result["research_priority"] = []
    result["ranking"] = {"status": "unavailable", "selected_symbols": [],
                         "research_priority": [], "errors": [], "source": None}
    result["coverage"].update(symbol_limit=MAX_SYMBOLS, requested_count=0,
                              price_range_count=0, price_available_count=0,
                              selection="explicit_symbols" if symbols is not None else "volume_momentum_ranked_local_export")
    if symbols is not None and ranking_input is not None:
        result["reasons"] = ["PUBLIC_CONFLICTING_SYMBOL_SELECTION"]
        return result
    if symbols is not None and (not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS or
            any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols) or
            len(set(symbols)) != len(symbols)):
        result["reasons"] = ["PUBLIC_SYMBOL_SELECTION_INVALID"]
        return result
    if symbols is None and ranking_input is None:
        result["reasons"] = ["RANKING_INPUT_REQUIRED"]
        result["ranking"]["errors"] = ["RANKING_INPUT_REQUIRED"]
        return result
    env = {"SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", "")} if environ is None else environ
    sources = sources or PublicSources(environ=env, now=now)
    market_source = market_source or FintableSource(transport=getattr(sources, "transport", None), now=now)
    universe = sources.universe()
    result["sources"].extend(universe.get("sources", []))
    listed = {e["symbol"]: e for e in universe.get("entries", [])}
    result["coverage"]["universe_count"] = len(listed)
    result["coverage"]["unobserved_count"] = len(listed)
    if universe.get("status") != "available":
        result["reasons"] = universe.get("errors", []) + ["PUBLIC_UNIVERSE_UNAVAILABLE"]
        return result
    if symbols is None:
        ranking = select_research_symbols(ranking_input, listed, clock())
        result["ranking"] = ranking
        ranking_source_index = len(result["sources"])
        if ranking.get("source"):
            result["sources"].append(ranking["source"])
        if ranking["status"] != "available":
            result["reasons"] = ranking["errors"]
            return result
        selected = ranking["selected_symbols"]
        result["research_priority"] = ranking["research_priority"]
    else:
        selected = symbols
        result["ranking"]["status"] = "not_requested_manual_selection"
    if not selected or any(s not in listed for s in selected):
        result["reasons"] = ["PUBLIC_LISTING_NOT_IN_DISCOVERED_UNIVERSE"]
        return result
    result["coverage"]["requested_count"] = len(selected)
    result["coverage"]["selected_symbols"] = selected
    fetched = market_source.prices(selected)
    result["sources"].extend(fetched.get("sources", []))
    result["reasons"].extend(fetched.get("errors", []))
    quotes = fetched.get("quotes", {})
    histories = {}
    in_range = []
    for symbol in selected:
        quote = quotes.get(symbol, {})
        price = quote.get("price")
        eligible_price = price is not None and engine.MIN_PRICE_USD <= Decimal(price) <= engine.MAX_PRICE_USD
        if eligible_price:
            in_range.append(symbol)
            history = market_source.history(symbol, clock().astimezone(NY).date().isoformat())
            histories[symbol] = {**history, "collected_at": clock()}
            result["sources"].extend(history.get("sources", []))
    # SEC metadata is useful even when a quote is stale, but is never clearance
    # for ATM/dilution. Missing identification causes no SEC network request.
    sec_results = {}
    if in_range:
        contact = env.get("SEC_USER_AGENT", "")
        if not isinstance(contact, str) or not contact.strip():
            sec_results = {s: {"status": "unknown", "flags": [], "error": "sec_user_agent_required"}
                           for s in in_range}
        else:
            mapping = sources.symbol_map()
            result["sources"].extend(mapping.get("sources", []))
            for symbol in in_range:
                entry = mapping.get("mapping", {}).get(symbol)
                if mapping.get("status") != "available":
                    sec_results[symbol] = {"status": "unknown", "flags": [], "error": "PUBLIC_SEC_SYMBOL_MAPPING_UNAVAILABLE"}
                elif not entry or entry.get("exchange") != listed[symbol]["exchange"]:
                    sec_results[symbol] = {"status": "unknown", "flags": [], "error": "PUBLIC_SEC_SYMBOL_EXCHANGE_MISMATCH"}
                else:
                    filing = sources.filings({"symbol": symbol, "cik": entry["cik"]}, clock())
                    sec_results[symbol] = filing
                    result["coverage"]["sec_review_count"] += 1
                    result["sources"].extend(filing.get("sources", []))
    finished = clock()
    if ranking_input is not None:
        # Recheck every source timestamp after potentially slow history/SEC
        # collection. Never backfill with unqueried runners-up at this point.
        final_ranking = select_research_symbols(ranking_input, listed, finished)
        result["ranking"] = final_ranking
        if final_ranking.get("source"):
            result["sources"][ranking_source_index] = final_ranking["source"]
        else:
            previous_source = result["sources"][ranking_source_index]
            result["sources"][ranking_source_index] = {
                **previous_source, "status": "unavailable",
                "evaluated_at": _stamp(finished),
                "retrieval_age_seconds": (finished - _dt(previous_source["retrieved_at"])).total_seconds(),
                "errors": final_ranking["errors"],
            }
        if (final_ranking["status"] != "available" or
                final_ranking["selected_symbols"] != selected):
            result["research_priority"] = []
            result["ranking"]["status"] = "unavailable"
            result["ranking"]["selected_symbols"] = []
            result["ranking"]["research_priority"] = []
            result["ranking"]["errors"] = list(dict.fromkeys(
                final_ranking["errors"] + ["RANKING_EXPIRED_DURING_COLLECTION"]))
            result["reasons"].append("RANKING_EXPIRED_DURING_COLLECTION")
        else:
            result["research_priority"] = final_ranking["research_priority"]
    for symbol in selected:
        quote = quotes.get(symbol, {})
        source_time, age = _time_info(quote.get("as_of"), finished)
        history = histories.get(symbol, {})
        metrics = _metrics(history.get("data"), quote.get("feed"), finished, history.get("collected_at"))
        filing = sec_results.get(symbol, {"status": "unknown", "flags": [], "error": "NOT_CHECKED_OUTSIDE_PRICE_FILTER_OR_MISSING_PRICE"})
        reasons = list(quote.get("errors", [])) + list(history.get("errors", []))
        reasons.extend(["SPREAD_UNAVAILABLE", "NEWS_CATALYST_UNVERIFIED", "SEC_FULL_DOCUMENT_REVIEW_REQUIRED",
                        "FINTABLE_DELAY_AND_PRICE_FALLBACK_NOT_VERIFIED", "CONSOLIDATED_VOLUME_AND_RVOL_UNAVAILABLE"])
        if not quote:
            reasons.append("PRICE_DATA_UNAVAILABLE")
        if age is None or not 0 <= age <= 30:
            reasons.append("PUBLIC_MARKET_DATA_STALE_OR_TIMESTAMP_UNVERIFIED")
        if quote.get("trading_day") != finished.astimezone(NY).date().isoformat():
            reasons.append("VOLUME_SESSION_STALE_OR_UNVERIFIED")
        if symbol not in in_range:
            reasons.append("PRICE_OUTSIDE_1_5_USD_OR_UNAVAILABLE")
        if filing.get("error"):
            reasons.append(filing["error"])
        if filing.get("flags"):
            reasons.append("SEC_FINANCING_RISK_OR_REVIEW_FLAG")
        day_change = None
        if quote.get("price") is not None and quote.get("previous_close") is not None:
            with localcontext(engine._CALCULATION_CONTEXT):
                day_change = str((Decimal(quote["price"]) / Decimal(quote["previous_close"]) - 1) * 100)
        result["securities"].append({
            "symbol": symbol, "exchange": listed[symbol]["exchange"], "decision": "PAS",
            "data_status": "DATA_UNAVAILABLE", "last_price_usd": quote.get("price"),
            "volume": quote.get("volume"), "volume_scope": "IEX_ONLY" if quote.get("feed") == "iex" else "UNKNOWN",
            "volume_session": quote.get("trading_day"), "source_timestamp": source_time,
            "source_url": "https://fintable.io/api/v2/prices?symbols=" + ",".join(selected),
            "retrieved_at": next((s.get("retrieved_at") for s in fetched.get("sources", []) if s.get("provider") == "Fintable"), None),
            "age_seconds": age, "delay_seconds": None, "feed": quote.get("feed"),
            "in_price_range": symbol in in_range,
            "price_filter_basis": "OBSERVED_PRICE_NOT_VERIFIED_CURRENT_ELIGIBILITY",
            "day_change_pct": day_change, "metrics": metrics,
            "quote": {"status": "DATA_UNAVAILABLE", "bid": None, "ask": None, "spread_usd": None},
            "news": {"status": "DATA_UNAVAILABLE", "headline": None, "url": None, "published_at": None},
            "sec": {k: v for k, v in filing.items() if k != "sources"},
            "reasons": list(dict.fromkeys(reasons)), "execution_enabled": False,
        })
    observed = {s for s in selected if quotes.get(s, {}).get("price") is not None}
    result["coverage"].update(observation_count=len(quotes), price_available_count=len(observed),
                              price_range_count=len(in_range), unobserved_count=len(set(listed) - observed))
    result["reasons"].append("NO_VERIFIED_WATCH_CANDIDATE")
    result["evaluated_at"] = _stamp(finished)
    return result
