"""Data-only inputs and a single fixed analysis job. No shell/subprocess/import API."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import threading
import time
import uuid

from equity_guard.engine import evaluate
from equity_guard.providers import collect
from .storage import Store, StoreError

ID = re.compile(r"[0-9a-f]{32}\Z")


class JobError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _keys(value, permitted, field):
    if not isinstance(value, dict) or set(value) - set(permitted):
        raise JobError("invalid_" + field)


def validate_context(context):
    """Allow incomplete evidence (PAS), reject executable/control/secret fields."""
    _keys(context, {"symbols", "fx", "account", "costs"}, "context")
    symbols = context.get("symbols", [])
    if not isinstance(symbols, list) or len(symbols) > 20:
        raise JobError("invalid_symbols")
    for symbol in symbols:
        _keys(symbol, {"symbol", "cik", "kind", "exchange", "broker_available", "broker_verified_at", "news_review", "filings_review"}, "symbol")
        if "news_review" in symbol and symbol["news_review"] is not None:
            _keys(symbol["news_review"], {"status", "id", "url", "published_at", "reviewed_at", "category", "material"}, "news_review")
        if "filings_review" in symbol and symbol["filings_review"] is not None:
            _keys(symbol["filings_review"], {"status", "checked_at", "source_url", "coverage_days", "reviewed_by", "latest_filing_date"}, "filings_review")
    for field, allowed in {
        "fx": {"usdtry_ask", "usdtry_bid", "as_of", "source"},
        "account": {"as_of", "session_date", "source", "pnl_scope", "cash_try", "realized_pnl_try", "unrealized_pnl_try", "open_risk_try", "reserved_try", "positions", "trading_allowed", "restrictions_verified_at"},
        "costs": {"entry_fee_try", "exit_fee_try", "slippage_pct", "source", "verified_at", "sub_dollar_exit_covered"},
    }.items():
        value = context.get(field)
        if value is not None:
            _keys(value, allowed, field)
    account = context.get("account") or {}
    if "positions" in account:
        positions = account["positions"]
        if not isinstance(positions, list) or len(positions) > 100:
            raise JobError("invalid_positions")
        for position in positions:
            _keys(position, {"symbol", "market_value_try"}, "position")

    def bounded(value, depth=0):
        if depth > 8:
            raise JobError("context_too_deep")
        if isinstance(value, str) and (len(value) > 4096 or "\x00" in value):
            raise JobError("context_string_invalid")
        if isinstance(value, dict):
            for child in value.values():
                bounded(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 100:
                raise JobError("context_list_too_large")
            for child in value:
                bounded(child, depth + 1)
    bounded(context)
    return context


def scan(context):
    """Only installed provider + deterministic risk engine. No order capability."""
    evidence = collect(context)
    result = evaluate(evidence)
    result["review_queue"] = [
        {"symbol": security.get("symbol"),
         "news_review_candidates": (security.get("news") or {}).get("review_candidates", []),
         "filings_status": (security.get("filings") or {}).get("status", "unknown"),
         "filings_flags": (security.get("filings") or {}).get("flags", [])}
        for security in evidence.get("securities", [])[:20]
    ]
    return result


class Jobs:
    def __init__(self, store: Store, runner=scan, interval=30):
        self.store = store
        self.runner = runner
        self.interval = interval
        self.lock = threading.RLock()
        self.current = None
        self.worker = None
        self.last_start = float("-inf")

    def upload(self, context):
        validate_context(context)
        object_id = uuid.uuid4().hex
        self.store.create("inputs", object_id, context)
        return {"id": object_id, "execution_enabled": False}

    def submit(self, payload):
        _keys(payload, {"action", "input_id", "request_id"}, "job")
        if payload.get("action") != "scan":
            raise JobError("only_scan_action_allowed")
        object_id, input_id = payload.get("request_id"), payload.get("input_id")
        if not isinstance(object_id, str) or not ID.fullmatch(object_id) or not isinstance(input_id, str) or not ID.fullmatch(input_id):
            raise JobError("invalid_object_id")
        with self.lock:
            if self.store.exists("jobs", object_id):
                existing = self.store.read("jobs", object_id)
                if existing.get("input_id") != input_id or existing.get("action") != "scan":
                    raise JobError("request_id_conflict")
                return self.status(object_id)
            context = self.store.read("inputs", input_id)
            validate_context(context)
            if self.current is not None:
                raise JobError("job_in_progress")
            instant = time.monotonic()
            if instant - self.last_start < self.interval:
                raise JobError("job_rate_limit")
            # Persist intent BEFORE running. Repeating a request_id never reruns it.
            self.store.create("jobs", object_id, {"id": object_id, "input_id": input_id, "action": "scan", "created_at": datetime.now(timezone.utc).isoformat()})
            self.current = object_id
            self.last_start = instant
            self.worker = threading.Thread(target=self._run, args=(object_id, context), daemon=True)
            self.worker.start()
            return {"id": object_id, "status": "running", "execution_enabled": False}

    def _run(self, object_id, context):
        try:
            result = self.runner(context)
            if not isinstance(result, dict) or result.get("execution_enabled") is not False or result.get("decision") not in {"PAS", "HAZIRLIK"}:
                raise ValueError("unsafe_runner_result")
            self.store.create("results", object_id, {"id": object_id, "status": "finished", "execution_enabled": False, "finished_at": datetime.now(timezone.utc).isoformat(), "result": result})
        except Exception:
            try:
                self.store.create("results", object_id, {"id": object_id, "status": "failed", "execution_enabled": False, "result": {"decision": "PAS", "execution_enabled": False, "candidates": [], "reasons": ["ANALYSIS_FAILED"]}})
            except Exception:
                pass  # Missing completion is reported as interrupted, never success.
        finally:
            with self.lock:
                self.current = None

    def status(self, object_id):
        with self.lock:
            intent = self.store.read("jobs", object_id)
            if self.store.exists("results", object_id):
                result = self.store.read("results", object_id)
                # Persisted analysis is evidence of a past scan, not current eligibility.
                result["historical_result"] = True
                result["requires_fresh_scan_before_manual_action"] = True
                return result
            return {"id": object_id, "status": "running" if self.current == object_id else "interrupted", "execution_enabled": False, "created_at": intent["created_at"]}

    def close(self):
        if self.worker:
            self.worker.join(timeout=2)
