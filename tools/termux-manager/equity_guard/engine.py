"""Conservative, deterministic screening. No networking or order execution.

All prices in tests are fictional. Inputs must carry current source attestations;
this module validates their shape and freshness, not the truth of an attestation.
Percentage fields use percentage points: 0.25 means 0.25 percent. Quote sizes
are shares. Net daily loss includes current-session realized and unrealized
P/L after fees, using prior-session close plus today's flows as the basis, not
lifetime open-position P/L. A current-session profitable position can offset
another position's current-session loss in that explicit net-loss rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CAPITAL_TRY = Decimal("50000")
POSITION_CAP_TRY = Decimal("12500")
DAILY_LOSS_LIMIT_TRY = Decimal("2500")
PLANNED_STOP_PCT = Decimal("3")
MIN_PRICE_USD = Decimal("1")
MAX_PRICE_USD = Decimal("5")
MAX_SPREAD_USD = Decimal("0.05")
MAX_SPREAD_RATIO = Decimal("0.025")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")
_ZERO = Decimal("0")
_CENT = Decimal("0.01")
# Each numeric representation is capped at 100 characters below. The longest
# money expression multiplies three such inputs and an integer share count;
# 512 digits retain their exact finite products and boundary differences. A
# private context also prevents caller/thread rounding settings from changing
# hard risk limits. Rounding to cents happens only for display or the stop.
_CALCULATION_CONTEXT = Context(prec=512)


def _object(value, path, errors):
    if not isinstance(value, dict):
        errors.append(f"{path}:MISSING_OR_INVALID_OBJECT")
        return {}
    return value


def _text(obj, key, path, errors):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key}:MISSING_OR_INVALID_TEXT")
        return None
    return value.strip()


def _number(obj, key, path, errors, *, minimum=None, maximum=None, positive=False,
            integer=False):
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        errors.append(f"{path}.{key}:MISSING_OR_INVALID_NUMBER")
        return None
    try:
        # Bound representation and magnitude before arithmetic or integer sizing.
        if len(str(value)) > 100:
            raise ValueError("oversized number")
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1e12"):
            raise ValueError("nonfinite or oversized number")
        if number and number.adjusted() < -12:
            raise ValueError("unsupported precision")
        if (minimum is not None and number < minimum or
                maximum is not None and number > maximum or
                positive and number <= 0 or
                integer and number != number.to_integral_value()):
            raise ValueError("outside permitted range")
    except (InvalidOperation, ValueError, OverflowError):
        errors.append(f"{path}.{key}:INVALID_NUMBER_OR_RANGE")
        return None
    return number


def _timestamp(obj, key, path, errors, now, max_age):
    raw = obj.get(key)
    try:
        if not isinstance(raw, str):
            raise ValueError("timestamp required")
        value = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        value = value.astimezone(timezone.utc)
        age = (now - value).total_seconds()
        if age < -5:
            errors.append(f"{path}.{key}:FUTURE_TIMESTAMP")
            return None
        if age > max_age:
            errors.append(f"{path}.{key}:STALE")
            return None
        return value
    except (TypeError, ValueError, OverflowError):
        errors.append(f"{path}.{key}:MISSING_OR_INVALID_AWARE_TIMESTAMP")
        return None


def _https_url(obj, key, path, errors):
    raw = _text(obj, key, path, errors)
    if raw is None:
        return None
    try:
        value = urlparse(raw)
        if (value.scheme != "https" or not value.hostname or value.username or
                value.password or any(ch.isspace() for ch in raw)):
            raise ValueError("HTTPS provenance URL required")
    except ValueError:
        errors.append(f"{path}.{key}:INVALID_HTTPS_URL")
        return None
    return raw


def _symbol(value):
    return isinstance(value, str) and _SYMBOL.fullmatch(value) is not None


def _decimal_text(value):
    # JSON strings preserve exact Decimal values and cannot encode NaN/Infinity.
    return format(value, "f")


def _money(value):
    return _decimal_text(value.quantize(_CENT, rounding=ROUND_CEILING))


def _security(item, index, now, context):
    errors = []
    path = f"securities[{index}]"
    security = _object(item, path, errors)
    symbol = security.get("symbol")
    if not _symbol(symbol):
        errors.append(f"{path}.symbol:INVALID_SYMBOL")
        symbol = f"INVALID_{index}"
    if security.get("exchange") not in ("NASDAQ", "NYSE", "AMEX"):
        errors.append(f"{path}.exchange:UNSUPPORTED_EXCHANGE")
    if security.get("kind") != "stock":
        errors.append(f"{path}.kind:COMMON_STOCK_REQUIRED")
    if security.get("broker_available") is not True:
        errors.append(f"{path}.broker_available:NOT_VERIFIED_AVAILABLE")
    _timestamp(security, "broker_verified_at", path, errors, now, 86400)

    quote = _object(security.get("quote"), path + ".quote", errors)
    _timestamp(quote, "as_of", path + ".quote", errors, now, 15)
    bid = _number(quote, "bid", path + ".quote", errors, positive=True)
    ask = _number(quote, "ask", path + ".quote", errors,
                  minimum=MIN_PRICE_USD, maximum=MAX_PRICE_USD)
    _number(quote, "bid_size", path + ".quote", errors, positive=True, integer=True)
    ask_size = _number(quote, "ask_size", path + ".quote", errors,
                       positive=True, integer=True)
    if bid is not None and ask is not None:
        spread = ask - bid
        if spread < 0:
            errors.append(f"{path}.quote:CROSSED_MARKET")
        elif spread > MAX_SPREAD_USD or spread > bid * MAX_SPREAD_RATIO:
            errors.append(f"{path}.quote:SPREAD_LIMIT_EXCEEDED")

    trade = _object(security.get("trade"), path + ".trade", errors)
    _timestamp(trade, "as_of", path + ".trade", errors, now, 30)
    price = _number(trade, "price", path + ".trade", errors,
                    minimum=MIN_PRICE_USD, maximum=MAX_PRICE_USD)
    metrics = _object(security.get("metrics"), path + ".metrics", errors)
    _timestamp(metrics, "as_of", path + ".metrics", errors, now, 60)
    window_end = _timestamp(metrics, "window_end", path + ".metrics", errors, now, 330)
    if (window_end is not None and
            window_end.astimezone(ZoneInfo("America/New_York")).date() !=
            now.astimezone(ZoneInfo("America/New_York")).date()):
        errors.append(f"{path}.metrics.window_end:NOT_CURRENT_NEW_YORK_SESSION")
    volume = _number(metrics, "day_volume", path + ".metrics", errors,
                     minimum=_ZERO, integer=True)
    dollar_volume = _number(metrics, "day_dollar_volume", path + ".metrics", errors,
                            minimum=_ZERO)
    rvol = _number(metrics, "rvol_5m", path + ".metrics", errors, minimum=_ZERO)
    momentum = _number(metrics, "return_5m_pct", path + ".metrics", errors)
    day_change = _number(metrics, "day_change_pct", path + ".metrics", errors)
    vwap = _number(metrics, "vwap", path + ".metrics", errors, positive=True)
    for value, threshold, name in (
            (volume, Decimal("1000000"), "DAY_VOLUME"),
            (dollar_volume, Decimal("2000000"), "DAY_DOLLAR_VOLUME"),
            (rvol, Decimal("2"), "RVOL_5M"),
            (momentum, Decimal("1"), "MOMENTUM_5M"),
            (day_change, Decimal("3"), "DAY_CHANGE")):
        if value is not None and value < threshold:
            errors.append(f"{path}.metrics:{name}_BELOW_DESIGN_THRESHOLD")
    if price is not None and vwap is not None and price <= vwap:
        errors.append(f"{path}.metrics:PRICE_NOT_ABOVE_VWAP")

    news = _object(security.get("news"), path + ".news", errors)
    if news.get("status") != "verified" or news.get("material") is not True:
        errors.append(f"{path}.news:UNVERIFIED_MATERIAL_CATALYST")
    if news.get("category") not in ("earnings", "fda", "contract", "merger", "guidance"):
        errors.append(f"{path}.news:UNSUPPORTED_CATALYST_CATEGORY")
    published = _timestamp(news, "published_at", path + ".news", errors, now, 86400)
    reviewed = _timestamp(news, "reviewed_at", path + ".news", errors, now, 86400)
    if published is not None and reviewed is not None and reviewed < published:
        errors.append(f"{path}.news:REVIEW_PRECEDES_PUBLICATION")
    _https_url(news, "url", path + ".news", errors)
    _text(news, "title", path + ".news", errors)

    filings = _object(security.get("filings"), path + ".filings", errors)
    if filings.get("status") != "clear":
        errors.append(f"{path}.filings:FINANCING_DILUTION_REVIEW_NOT_CLEAR")
    _timestamp(filings, "checked_at", path + ".filings", errors, now, 86400)
    _https_url(filings, "source_url", path + ".filings", errors)
    _text(filings, "reviewed_by", path + ".filings", errors)
    _number(filings, "coverage_days", path + ".filings", errors,
            minimum=Decimal("365"), integer=True)
    if not isinstance(filings.get("flags"), list) or filings.get("flags") != []:
        errors.append(f"{path}.filings:FINANCING_DILUTION_FLAGS_OR_UNKNOWN")

    diagnostic = {"symbol": symbol, "eligible": False, "reasons": errors}
    if context is None:
        errors.append("GLOBAL_DATA_OR_RISK_CHECK_FAILED")
        return diagnostic, None
    if errors:
        return diagnostic, None

    stop = (ask * Decimal("0.97")).quantize(_CENT, rounding=ROUND_CEILING)
    if stop < 1 and not context["sub_dollar_exit_covered"]:
        errors.append(f"{path}:SUB_DOLLAR_EXIT_COST_NOT_VERIFIED")
        return diagnostic, None
    entry_per_share_try = ask * context["fx_ask"]
    slippage_per_share_try = entry_per_share_try * context["slippage_pct"] / 100
    loss_per_share_try = (entry_per_share_try - stop * context["fx_bid"] +
                          slippage_per_share_try)
    existing = context["positions"].get(symbol, _ZERO)
    entry_fee = context["entry_fee"]
    exit_fee = context["exit_fee"]
    spend_budget = min(
        POSITION_CAP_TRY - existing - entry_fee,
        context["cash"] - context["reserved"] - entry_fee,
        CAPITAL_TRY - context["position_total"] - context["reserved"] - entry_fee,
    )
    risk_budget = (DAILY_LOSS_LIMIT_TRY - context["daily_loss"] -
                   context["open_risk"] - entry_fee - exit_fee)
    if spend_budget <= 0 or risk_budget <= 0 or loss_per_share_try <= 0:
        errors.append(f"{path}:NO_REMAINING_CASH_POSITION_CAPITAL_OR_RISK_BUDGET")
        return diagnostic, None
    quantity = int(min(spend_budget / entry_per_share_try,
                       risk_budget / loss_per_share_try).to_integral_value(rounding=ROUND_FLOOR))
    if quantity < 1:
        errors.append(f"{path}:BUDGET_BELOW_ONE_SHARE")
        return diagnostic, None
    if quantity > ask_size:
        errors.append(f"{path}:INSUFFICIENT_DISPLAYED_ASK_LIQUIDITY")
        return diagnostic, None
    notional = quantity * entry_per_share_try
    entry_debit = notional + entry_fee
    estimated_loss = quantity * loss_per_share_try + entry_fee + exit_fee
    diagnostic["eligible"] = True
    candidate = {
        "symbol": symbol,
        "status": "DRAFT_REQUIRES_MANUAL_REVIEW",
        "execution_enabled": False,
        "manual_review_required": [
            "CURRENT_HALT_LULD_STATUS",
            "BROKER_ORDER_AND_STOP_SUPPORT",
            "INDEPENDENT_ACCOUNT_AND_OPEN_RISK_RECONCILIATION",
        ],
        "quantity": quantity,
        "entry_limit_usd": _decimal_text(ask),
        "planned_stop_usd": _decimal_text(stop),
        "planned_stop_pct": "3",
        "actual_stop_distance_pct": _decimal_text((ask - stop) / ask * 100),
        "usdtry_ask": _decimal_text(context["fx_ask"]),
        "usdtry_bid": _decimal_text(context["fx_bid"]),
        "entry_notional_try": _money(notional),
        "entry_fee_try": _money(entry_fee),
        "entry_debit_try": _money(entry_debit),
        "exit_fee_try": _money(exit_fee),
        "slippage_allowance_try": _money(quantity * slippage_per_share_try),
        "estimated_loss_try": _money(estimated_loss),
        "daily_loss_try": _money(context["daily_loss"]),
        "existing_open_risk_try": _money(context["open_risk"]),
        "total_day_risk_try": _money(context["daily_loss"] + context["open_risk"] + estimated_loss),
        "post_trade_symbol_commitment_try": _money(existing + entry_debit),
        "remaining_cash_try": _decimal_text(
            (context["cash"] - context["reserved"] - entry_debit).quantize(
                _CENT, rounding=ROUND_FLOOR)),
        "news_url": news["url"],
        "filings_url": filings["source_url"],
        "rank_metrics": {
            "rvol_5m": _decimal_text(rvol),
            "return_5m_pct": _decimal_text(momentum),
            "day_change_pct": _decimal_text(day_change),
        },
        "risk_note": "Planned stop is not a guaranteed fill; gaps, halts and execution costs can increase loss.",
        "_rank": (rvol, momentum, day_change, dollar_volume),
    }
    return diagnostic, candidate


def _evaluate(bundle, now):
    errors = []
    bundle = _object(bundle, "bundle", errors)
    mode = bundle.get("mode")
    simulated = mode == "simulation" or bundle.get("simulated") is True
    result = {
        "decision": "PAS", "execution_enabled": False, "simulated": simulated,
        "evaluated_at": now.isoformat(), "reasons": errors,
        "candidates": [], "securities": [],
    }
    if mode not in ("live", "simulation"):
        errors.append("bundle.mode:LIVE_OR_SIMULATION_REQUIRED")
    if "simulated" in bundle and not isinstance(bundle["simulated"], bool):
        errors.append("bundle.simulated:BOOLEAN_REQUIRED")
    upstream = bundle.get("errors")
    if not isinstance(upstream, list) or any(not isinstance(x, str) for x in upstream):
        errors.append("bundle.errors:MISSING_OR_INVALID_ERROR_LIST")
    elif upstream:
        errors.extend("UPSTREAM_ERROR:" + error for error in upstream)
    source = _object(bundle.get("quote_source"), "quote_source", errors)
    if (source.get("provider") != "alpaca" or source.get("feed") != "sip" or
            source.get("realtime") is not True):
        errors.append("quote_source:VERIFIED_REALTIME_ALPACA_SIP_REQUIRED")

    ny_date = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    market = _object(bundle.get("market"), "market", errors)
    _timestamp(market, "as_of", "market", errors, now, 60)
    _text(market, "source", "market", errors)
    if market.get("is_open") is not True:
        errors.append("market:REGULAR_SESSION_NOT_VERIFIED_OPEN")
    if market.get("session_date") != ny_date:
        errors.append("market.session_date:NOT_CURRENT_NEW_YORK_DATE")

    fx = _object(bundle.get("fx"), "fx", errors)
    _timestamp(fx, "as_of", "fx", errors, now, 300)
    _text(fx, "source", "fx", errors)
    fx_ask = _number(fx, "usdtry_ask", "fx", errors, positive=True)
    fx_bid = _number(fx, "usdtry_bid", "fx", errors, positive=True)
    if fx_ask is not None and fx_bid is not None and fx_bid > fx_ask:
        errors.append("fx:CROSSED_FX_MARKET")

    account = _object(bundle.get("account"), "account", errors)
    _timestamp(account, "as_of", "account", errors, now, 300)
    _timestamp(account, "restrictions_verified_at", "account", errors, now, 300)
    _text(account, "source", "account", errors)
    if account.get("trading_allowed") is not True:
        errors.append("account:TRADING_RESTRICTIONS_OR_SETTLEMENT_NOT_CLEARED")
    if account.get("session_date") != ny_date:
        errors.append("account.session_date:NOT_CURRENT_NEW_YORK_DATE")
    if account.get("pnl_scope") != "current_session_net_fees":
        errors.append("account.pnl_scope:CURRENT_SESSION_NET_FEES_REQUIRED")
    cash = _number(account, "cash_try", "account", errors, minimum=_ZERO)
    realized = _number(account, "realized_pnl_try", "account", errors)
    unrealized = _number(account, "unrealized_pnl_try", "account", errors)
    open_risk = _number(account, "open_risk_try", "account", errors, minimum=_ZERO)
    reserved = _number(account, "reserved_try", "account", errors, minimum=_ZERO)
    if reserved is not None and reserved > 0:
        # The initial schema has no per-symbol pending-order commitments or
        # pending-order loss attribution. Cash subtraction alone cannot prove
        # the symbol limit or remaining risk budget after an outstanding fill.
        errors.append("account:PENDING_ORDER_RESERVATIONS_REQUIRE_RECONCILIATION")
    positions = {}
    raw_positions = account.get("positions")
    if not isinstance(raw_positions, list) or len(raw_positions) > 1000:
        errors.append("account.positions:MISSING_OR_INVALID_POSITIONS")
    else:
        for index, position in enumerate(raw_positions):
            path = f"account.positions[{index}]"
            position = _object(position, path, errors)
            symbol = position.get("symbol")
            value = _number(position, "market_value_try", path, errors, minimum=_ZERO)
            if not _symbol(symbol):
                errors.append(path + ".symbol:INVALID_SYMBOL")
            elif value is not None:
                positions[symbol] = positions.get(symbol, _ZERO) + value
    if any(value > POSITION_CAP_TRY for value in positions.values()):
        errors.append("account:EXISTING_POSITION_CAP_EXCEEDED")
    daily_loss = None
    if realized is not None and unrealized is not None:
        daily_loss = max(_ZERO, -(realized + unrealized))
        if daily_loss >= DAILY_LOSS_LIMIT_TRY:
            errors.append("account:DAILY_LOSS_LIMIT_REACHED")
        if open_risk is not None and daily_loss + open_risk >= DAILY_LOSS_LIMIT_TRY:
            errors.append("account:DAILY_RISK_BUDGET_EXHAUSTED")

    costs = _object(bundle.get("costs"), "costs", errors)
    _text(costs, "source", "costs", errors)
    _timestamp(costs, "verified_at", "costs", errors, now, 86400)
    entry_fee = _number(costs, "entry_fee_try", "costs", errors, minimum=_ZERO)
    exit_fee = _number(costs, "exit_fee_try", "costs", errors, minimum=_ZERO)
    slippage = _number(costs, "slippage_pct", "costs", errors,
                       minimum=_ZERO, maximum=Decimal("100"))
    if not isinstance(costs.get("sub_dollar_exit_covered"), bool):
        errors.append("costs.sub_dollar_exit_covered:BOOLEAN_REQUIRED")
    context = None if errors else {
        "fx_ask": fx_ask, "fx_bid": fx_bid, "cash": cash,
        "reserved": reserved, "positions": positions,
        "position_total": sum(positions.values(), _ZERO),
        "daily_loss": daily_loss, "open_risk": open_risk,
        "entry_fee": entry_fee, "exit_fee": exit_fee, "slippage_pct": slippage,
        "sub_dollar_exit_covered": costs.get("sub_dollar_exit_covered") is True,
    }
    securities = bundle.get("securities")
    if not isinstance(securities, list) or len(securities) > 1000:
        errors.append("bundle.securities:MISSING_OR_INVALID_SECURITY_LIST")
        securities = []
    if not securities:
        errors.append("NO_SECURITIES")
    valid = []
    seen = set()
    for index, security in enumerate(securities):
        diagnostic, candidate = _security(security, index, now, context)
        symbol = diagnostic["symbol"]
        if symbol in seen:
            errors.append("DUPLICATE_SECURITY:" + symbol)
            diagnostic["eligible"] = False
            diagnostic["reasons"].append("DUPLICATE_SECURITY")
            candidate = None
        seen.add(symbol)
        result["securities"].append(diagnostic)
        if candidate is not None:
            valid.append(candidate)
    if simulated:
        errors.append("SIMULATION_INPUT_NOT_TRADABLE")
    if not valid:
        errors.append("NO_ELIGIBLE_SECURITY")
    if not errors and valid:
        best = max(valid, key=lambda item: item["_rank"])
        del best["_rank"]
        result["candidates"] = [best]
        result["decision"] = "HAZIRLIK"
    return result


def evaluate(bundle: dict, now: datetime | None = None) -> dict:
    """Return one analysis draft or PAS; order authority is always disabled.

    Missing, malformed, stale, simulated or risk-blocked data never produces an
    actionable candidate. Limits are constants and cannot be changed by input.
    """
    try:
        instant = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("timezone-aware evaluation time required")
        with localcontext(_CALCULATION_CONTEXT):
            return _evaluate(bundle, instant.astimezone(timezone.utc))
    except ZoneInfoNotFoundError:
        reason = "NEW_YORK_TIMEZONE_DATABASE_UNAVAILABLE"
    except (ValueError, TypeError, InvalidOperation, OverflowError, ArithmeticError, KeyError):
        # Invalid public inputs must fail closed, including unexpected numeric
        # precision. Never return partial candidates after validation failures.
        reason = "INVALID_INPUT_EVALUATION_ABORTED"
    return {"decision": "PAS", "execution_enabled": False,
            "simulated": isinstance(bundle, dict) and (
                bundle.get("mode") == "simulation" or bundle.get("simulated") is True),
            "reasons": [reason], "candidates": [], "securities": []}
