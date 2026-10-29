"""Timestamped forward-test checks, independent of retrospective replay.

This is a preparation/observation journal, not a trained forecasting model.
It never converts scanner ranks, simulated fills or incomplete history into a
probability. There is no broker, Telegram, scheduler or order connection.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
PROTOCOL = {
    "id": "us_1_5_orb_research_20261029_v2",
    "price_min_usd": 1, "price_max_usd": 5,
    "opening_range_minutes": 15, "signal_timeframe_minutes": 5,
    "first_signal_close_et": "09:50", "last_signal_close_et": "10:30",
    "volume_multiple_previous_3_completed_bars": 1.5,
    "stop_below_signal_low_usd": 0.01,
    "min_risk_usd": 0.02, "max_risk_usd": 0.08,
    "max_risk_fraction": 0.03, "target_r": 2,
    "max_holding_minutes": 20,
    "live_entry": "first_eligible_observed_quote_after_forecast_is_persisted",
    "prediction_target": "target_before_stop_within_20_minutes_after_entry",
    "validation": "not_trained_not_validated",
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_timestamp_required")
    return value.astimezone(UTC)


def _iso(value):
    return _aware(value).isoformat().replace("+00:00", "Z")


def make_check(*, history_manifest=None, connection_available=False, now=None):
    """Report actual readiness, without inventing a forecast or training result.

    ``now`` is an injected test clock. The CLI does not accept a past clock.
    The opening-window check is only an additional exclusion; it does not
    establish an exchange holiday calendar or that the market is open.
    """
    instant = _aware(now or datetime.now(UTC))
    local = instant.astimezone(NY)
    reasons = ["TRAINED_FORECAST_MODEL_NOT_IMPLEMENTED"]
    if not history_manifest or history_manifest.get("retrieval_complete") is not True:
        reasons.append("ONE_YEAR_HISTORY_NOT_DOWNLOADED")
    if not history_manifest or history_manifest.get("training_ready") is not True:
        reasons.append("HISTORICAL_COVERAGE_NOT_VALIDATED")
    if not connection_available:
        reasons.append("LIVE_DATA_CONNECTION_UNAVAILABLE")
    if local.weekday() >= 5 or not time(9, 50) <= local.time() < time(10, 30, 30):
        reasons.append("OPENING_STRATEGY_WINDOW_CLOSED")
    return {
        "schema_version": 1, "event_type": "readiness_check",
        "generated_at": _iso(instant), "session_date_et": local.date().isoformat(),
        "status": "BLOCKED", "reasons": reasons,
        "protocol": dict(PROTOCOL),
        "protocol_sha256": hashlib.sha256(_canonical(PROTOCOL).encode()).hexdigest(),
        "predictions": [], "probability": None,
        "model_trained": False, "one_year_backtest_executed": False,
        "live_forecasting_started": False, "execution_enabled": False,
        "whole_market_coverage_verified": False,
        "history_retrieval_complete": bool(history_manifest and history_manifest.get("retrieval_complete") is True),
        "history_training_ready_claim": bool(history_manifest and history_manifest.get("training_ready") is True),
    }


class ForwardJournal:
    """Serialize append-only readiness events with a hash chain.

    SQLite triggers prevent ordinary updates/deletes; the hash chain detects
    edited interior records. This is not an independently anchored audit log:
    a local owner can replace the database or delete its tail.
    """

    def __init__(self, path, *, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(UTC))
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS forward_events (
                    sequence INTEGER PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS forward_no_update
                BEFORE UPDATE ON forward_events BEGIN
                    SELECT RAISE(ABORT, 'append_only'); END;
                CREATE TRIGGER IF NOT EXISTS forward_no_delete
                BEFORE DELETE ON forward_events BEGIN
                    SELECT RAISE(ABORT, 'append_only'); END;
            """)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def _digest(sequence, recorded_at, payload, previous):
        material = _canonical([sequence, recorded_at, payload, previous])
        return hashlib.sha256(material.encode()).hexdigest()

    def append_check(self, event):
        # A readiness journal cannot be used to launder a simulated forecast.
        event = json.loads(_canonical(event))
        if (event.get("event_type") != "readiness_check" or event.get("status") != "BLOCKED" or
                event.get("predictions") != [] or event.get("probability") is not None or
                event.get("execution_enabled") is not False or
                event.get("live_forecasting_started") is not False or
                event.get("model_trained") is not False):
            raise ValueError("readiness_check_only")
        if (event.get("protocol") != PROTOCOL or
                event.get("protocol_sha256") != hashlib.sha256(_canonical(PROTOCOL).encode()).hexdigest()):
            raise ValueError("protocol_mismatch")
        payload = _canonical(event)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Capture real time after acquiring the writer lock. Otherwise
            # concurrent processes can appear to move the clock backwards.
            now = _aware(self.clock())
            age = (now - _aware(event.get("generated_at"))).total_seconds()
            if not 0 <= age <= 60:
                raise ValueError("check_stale_or_future")
            recorded_at = _iso(now)
            last = db.execute("SELECT sequence, event_hash, recorded_at FROM forward_events ORDER BY sequence DESC LIMIT 1").fetchone()
            if last and now < _aware(last[2]):
                raise ValueError("journal_clock_moved_backwards")
            sequence = last[0] + 1 if last else 1
            previous = last[1] if last else "0" * 64
            digest = self._digest(sequence, recorded_at, payload, previous)
            db.execute("INSERT INTO forward_events VALUES (?, ?, ?, ?, ?)",
                       (sequence, recorded_at, payload, previous, digest))
        return {"sequence": sequence, "recorded_at": recorded_at,
                "event_sha256": digest, "event": event}

    def verify(self):
        previous, expected = "0" * 64, 1
        with self._connect() as db:
            rows = db.execute("SELECT sequence, recorded_at, payload, previous_hash, event_hash FROM forward_events ORDER BY sequence")
            for sequence, recorded_at, payload, predecessor, digest in rows:
                if (sequence != expected or predecessor != previous or
                        digest != self._digest(sequence, recorded_at, payload, predecessor)):
                    return False
                previous, expected = digest, expected + 1
        return True
