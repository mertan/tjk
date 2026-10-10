"""Fail-closed source freshness and observational stock risk checks."""
from datetime import datetime
from decimal import Decimal
import re
from urllib.parse import urlsplit

from .models import Candidate, REQUIRED_EVIDENCE, SourceEvidence, StockRiskInputs

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
REASON = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
CAPITAL_TL = Decimal("50000")
MAX_POSITION_TL = Decimal("12500")
PLANNED_STOP = Decimal("0.03")
DAILY_LOSS_LIMIT_TL = Decimal("2500")
MAX_SPREAD_USD = Decimal("0.05")
MAX_SPREAD_FRACTION = Decimal("0.025")


def aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def stock_reasons(risk):
    if not isinstance(risk, StockRiskInputs):
        return ["RISK_DATA_UNAVAILABLE"]
    values = (risk.price, risk.bid, risk.ask, risk.usd_try, risk.position_tl,
              risk.open_exposure_tl, risk.daily_loss_tl, risk.relative_volume, risk.momentum_percent)
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
        return ["INVALID_RISK_DATA"]
    if any(abs(v) > Decimal("1e12") or v.as_tuple().exponent < -8 for v in values):
        return ["INVALID_RISK_DATA"]
    reasons = []
    if not Decimal("1") <= risk.price <= Decimal("5"):
        reasons.append("PRICE_OUT_OF_RANGE")
    if risk.bid <= 0 or risk.ask < risk.bid:
        reasons.append("INVALID_NBBO")
    else:
        spread = risk.ask - risk.bid
        # Bid denominator is conservative and avoids rounding acceptance at the cap.
        if spread > MAX_SPREAD_USD or spread > risk.bid * MAX_SPREAD_FRACTION:
            reasons.append("SPREAD_LIMIT")
    if risk.usd_try <= 0 or not 0 < risk.position_tl <= MAX_POSITION_TL:
        reasons.append("POSITION_LIMIT")
    if risk.open_exposure_tl < 0 or risk.open_exposure_tl + risk.position_tl > CAPITAL_TL:
        reasons.append("CAPITAL_LIMIT")
    if risk.daily_loss_tl < 0 or risk.daily_loss_tl >= DAILY_LOSS_LIMIT_TL:
        reasons.append("DAILY_LOSS_LIMIT")
    if risk.daily_loss_tl + risk.position_tl * PLANNED_STOP > DAILY_LOSS_LIMIT_TL:
        reasons.append("DAILY_RISK_BUDGET")
    if risk.relative_volume < 2 or risk.momentum_percent <= 0:
        reasons.append("WEAK_VOLUME_OR_MOMENTUM")
    if risk.positive_news is not True:
        reasons.append("POSITIVE_NEWS_UNVERIFIED")
    if risk.sec_financing_clear is not True:
        reasons.append("SEC_FINANCING_RISK_UNCLEARED")
    if risk.regular_session_open is not True:
        reasons.append("MARKET_CLOSED")
    return reasons


def assess(candidate, sport, now, source_hosts):
    """Return safe reason codes and whitelisted metadata; never expose raw payloads."""
    if not isinstance(candidate, Candidate) or not aware(now):
        return ("INVALID_PROVIDER_DATA",), ()
    reasons = []
    if not isinstance(candidate.reason_codes, tuple) or len(candidate.reason_codes) > 16 or any(
        not isinstance(r, str) or not REASON.fullmatch(r) for r in candidate.reason_codes
    ):
        reasons.append("INVALID_PROVIDER_REASONS")
    else:
        reasons.extend(candidate.reason_codes)
    if candidate.inputs_complete is not True:
        reasons.append("MANDATORY_DATA_MISSING")
    if not all(identifier(v) for v in (candidate.event_id, candidate.market, candidate.selection)):
        reasons.append("MISSING_PREDICTION_DATA")
    if not aware(candidate.closes_at):
        reasons.append("EVENT_TIME_UNVERIFIED")
    elif candidate.closes_at <= now:
        reasons.append("PREDICTION_CLOSED")
    if not isinstance(candidate.evidence, tuple) or len(candidate.evidence) > 16:
        return tuple(dict.fromkeys(reasons + ["INVALID_SOURCE_EVIDENCE"])), ()
    required = REQUIRED_EVIDENCE[sport]
    seen, provenance = set(), []
    for evidence in candidate.evidence:
        if not isinstance(evidence, SourceEvidence):
            reasons.append("INVALID_SOURCE_EVIDENCE")
            continue
        if not isinstance(evidence.name, str) or evidence.name not in required or evidence.name in seen:
            # This also rejects AGF and arbitrary provider feature additions.
            reasons.append("UNSUPPORTED_OR_DUPLICATE_INPUT")
            continue
        seen.add(evidence.name)
        try:
            url = urlsplit(evidence.source_url)
            valid_source = (url.scheme == "https" and url.hostname in source_hosts
                            and url.username is None and url.password is None
                            and not url.query and not url.fragment and url.port in (None, 443)
                            and len(evidence.source_url) <= 512 and identifier(evidence.source_id)
                            and not any(ord(c) < 32 for c in evidence.source_url))
        except (TypeError, ValueError, AttributeError):
            valid_source = False
        if not valid_source or evidence.verified is not True:
            reasons.append("SOURCE_UNVERIFIED")
            continue
        if not aware(evidence.as_of):
            reasons.append("SOURCE_TIME_UNVERIFIED")
            continue
        age = (now - evidence.as_of).total_seconds()
        delay = evidence.delay_seconds
        if type(delay) is not int or delay < 0:
            reasons.append("SOURCE_DELAY_UNVERIFIED")
            continue
        if age < 0:
            reasons.append("SOURCE_TIME_IN_FUTURE")
            continue
        elif age > required[evidence.name] or delay > required[evidence.name]:
            reasons.append("STALE_DATA")
        if sport == "hisse" and evidence.name in ("prices", "nbbo") and delay != 0:
            reasons.append("REALTIME_QUOTES_UNVERIFIED")
        provenance.append({"name": evidence.name, "source_id": evidence.source_id,
                           "source_url": evidence.source_url, "as_of": evidence.as_of,
                           "delay_seconds": delay})
    if required.keys() - seen:
        reasons.append("MANDATORY_DATA_MISSING")
    if sport == "hisse":
        reasons.extend(stock_reasons(candidate.stock_risk))
    return tuple(dict.fromkeys(reasons)), tuple(provenance)
