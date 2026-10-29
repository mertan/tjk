"""Bounded, GET-only Alpaca raw minute-bar export for an explicit universe.

``complete`` describes extraction, never market-wide or calendar coverage.
Point-in-time membership, delisted names and corporate actions are unverified;
these exports cannot by themselves establish model-training readiness.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .providers import DATA, ProviderError, ReadOnlyProvider, SYMBOL_RE

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
BAR_LIMIT = 10000
_SAFE_ERRORS = frozenset({"network_unavailable", "redirect_rejected",
    "response_too_large", "collection_budget_exceeded", "invalid_json",
    "invalid_json_shape", "unexpected_http_status", "endpoint_rejected"})


def _stamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware time required")
    return value.astimezone(UTC)


def _safe_error(exc):
    code = str(exc) if isinstance(exc, ProviderError) else "provider_request_failed"
    if code in _SAFE_ERRORS or code in {"http_400", "http_401", "http_403", "http_404", "http_422", "http_429", "http_500", "http_502", "http_503", "http_504"}:
        return code
    return "provider_request_failed"


def _number(value, *, integer=False, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid numeric field")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError("invalid numeric field")
    if integer and int(value) != value:
        raise ValueError("invalid integer field")
    return value


def _row(bar, start, end, previous):
    if not isinstance(bar, dict):
        raise ValueError("invalid bar")
    stamp = _instant(bar["t"])
    if stamp.second or stamp.microsecond or not start <= stamp < end or previous is not None and stamp <= previous:
        raise ValueError("out of range or unordered timestamp")
    opening, high, low, close = [_number(bar[k]) for k in ("o", "h", "l", "c")]
    volume = _number(bar["v"], integer=True, positive=False)
    if not low <= min(opening, close) <= max(opening, close) <= high:
        raise ValueError("invalid OHLC")
    vwap = _number(bar["vw"]) if "vw" in bar else ""
    count = _number(bar["n"], integer=True, positive=False) if "n" in bar else ""
    return stamp, [_stamp(stamp), opening, high, low, close, int(volume), vwap, count]


def _chunks(start, end):
    while start < end:
        next_month = start.replace(year=start.year + (start.month == 12),
            month=1 if start.month == 12 else start.month + 1, day=1,
            hour=0, minute=0, second=0, microsecond=0)
        stop = min(next_month, end)
        yield start, stop
        start = stop


def _credentials(env):
    # Do not combine halves of separate naming conventions or accounts.
    for first, second in (("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"),
                          ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")):
        key, secret = env.get(first, ""), env.get(second, "")
        if isinstance(key, str) and key and isinstance(secret, str) and secret:
            return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    return None


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def download_history(symbols, output_dir, *, start=None, end=None, now=None,
                     getter=None, environ=None, max_requests=600,
                     max_pages_per_chunk=20, seconds_budget=120):
    """Export [start,end) in monthly chunks, retaining every observed raw bar.

    Defaults cover one calendar year ending at today's New York midnight.
    The destination must be new; previous evidence is never overwritten.
    No price filter, trading call, current-universe discovery or training occurs.
    ``getter(url, headers, params)`` can inject a synthetic JSON transport.
    """
    manifest = {"schema_version": 1, "provider": "alpaca", "feed": "sip",
        "adjustment": "raw", "timeframe": "1Min", "complete": False,
        "retrieval_complete": False, "training_ready": False,
        "model_training_ready": False, "execution_enabled": False,
        "scope": "explicit_symbols_only_max_20", "pit_universe_verified": False,
        "delisted_coverage_verified": False, "splits_verified": False,
        "expected_bar_coverage_verified": False, "chunks": [], "errors": [],
        "requests": 0, "rows": 0}
    try:
        clock = _instant(datetime.now(UTC) if now is None else now)
        default_end = clock.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        last = _instant(end) if end is not None else default_end
        try:
            first = last.replace(year=last.year - 1)
        except ValueError:
            first = last.replace(year=last.year - 1, day=28)
        first = _instant(start) if start is not None else first
        if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 20 or
                any(not isinstance(s, str) or not SYMBOL_RE.fullmatch(s) for s in symbols) or
                len(set(symbols)) != len(symbols) or first >= last or
                last > clock or (last - first).days > 366 or
                any(t.second or t.microsecond for t in (first, last)) or
                type(max_requests) is not int or not 1 <= max_requests <= 5000 or
                type(max_pages_per_chunk) is not int or not 1 <= max_pages_per_chunk <= 100 or
                isinstance(seconds_budget, bool) or not isinstance(seconds_budget, (int, float)) or
                not math.isfinite(seconds_budget) or not 0 < seconds_budget <= 3600):
            raise ValueError("invalid request")
        manifest.update(symbols=list(symbols), start=_stamp(first), end_exclusive=_stamp(last),
                        created_at=_stamp(clock))
    except (ValueError, TypeError, OverflowError):
        manifest["errors"] = ["invalid_request"]
        return manifest
    headers = _credentials(os.environ if environ is None else environ)
    if headers is None:
        manifest["errors"] = ["credentials_missing"]
        return manifest
    provider = ReadOnlyProvider(getter=getter, environ={})
    provider._deadline = time.monotonic() + seconds_budget
    try:
        directory = Path(output_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except (TypeError, ValueError, OSError):
        manifest["errors"] = ["output_unavailable"]
        return manifest
    stopped = False
    for symbol in symbols:
        if stopped:
            break
        for begin, finish in _chunks(first, last):
            item = {"symbol": symbol, "start": _stamp(begin), "end_exclusive": _stamp(finish),
                    "rows": 0, "pages": 0, "complete": False, "error": None}
            manifest["chunks"].append(item)
            filename = symbol + "_" + begin.strftime("%Y%m%dT%H%M") + ".csv"
            path = directory / filename
            previous, tokens = None, set()
            params = {"symbols": symbol, "timeframe": "1Min", "start": _stamp(begin),
                      "end": _stamp(finish - timedelta(microseconds=1)),
                      "adjustment": "raw", "feed": "sip", "limit": BAR_LIMIT, "sort": "asc"}
            try:
                with path.open("x", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "vwap", "trade_count"])
                    for _ in range(max_pages_per_chunk):
                        if manifest["requests"] >= max_requests:
                            item["error"] = "request_budget_exceeded"
                            break
                        manifest["requests"] += 1
                        try:
                            payload = provider._get(DATA + "/v2/stocks/bars", headers, dict(params))
                        except Exception as exc:
                            item["error"] = _safe_error(exc)
                            break
                        item["pages"] += 1
                        if (not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict) or
                                set(payload["bars"]) - {symbol} or not isinstance(payload["bars"].get(symbol, []), list)):
                            item["error"] = "bars_schema_invalid"
                            break
                        batch = payload["bars"].get(symbol, [])
                        if len(batch) > BAR_LIMIT:
                            item["error"] = "bar_limit_exceeded"
                            break
                        for bar in batch:
                            try:
                                previous, row = _row(bar, begin, finish, previous)
                            except (KeyError, ValueError, TypeError, OverflowError):
                                item["error"] = "bar_validation_failed"
                                break
                            writer.writerow(row)
                            item["rows"] += 1
                        if item["error"]:
                            break
                        token = payload.get("next_page_token")
                        if token is None:
                            if item["rows"]:
                                item["complete"] = True
                            else:
                                item["error"] = "empty_chunk_unverified"
                            break
                        if (not isinstance(token, str) or not 1 <= len(token) <= 2048 or
                                token in tokens or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                            item["error"] = "pagination_invalid"
                            break
                        tokens.add(token)
                        params["page_token"] = token
                    else:
                        item["error"] = "pagination_limit_reached"
                item.update(file=filename, sha256=_digest(path))
            except OSError:
                item["error"] = "output_unavailable"
                item["complete"] = False
            manifest["rows"] += item["rows"]
            if not item["complete"]:
                manifest["errors"].append(item["error"] or "incomplete_chunk")
                stopped = True
                break
    manifest["complete"] = manifest["retrieval_complete"] = not manifest["errors"]
    manifest["limitations"] = ["EXTRACTION_COMPLETENESS_IS_NOT_CALENDAR_OR_MARKET_COVERAGE",
        "NO_POINT_IN_TIME_UNIVERSE_DELISTING_OR_SPLIT_VERIFICATION",
        "NO_CURRENT_PRICE_FILTER_ALL_OBSERVED_BARS_RETAINED",
        "NOT_READY_FOR_MODEL_TRAINING_OR_LIVE_SIGNALS"]
    try:
        with (directory / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except OSError:
        manifest["complete"] = manifest["retrieval_complete"] = False
        manifest["errors"].append("manifest_write_failed")
    return manifest
