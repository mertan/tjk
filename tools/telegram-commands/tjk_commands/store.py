"""Private, append-only prediction ledger. No network or Telegram transport.

Result verifiers are trusted application callbacks, not user-supplied flags. A
verifier must validate authoritative final evidence before returning its value.
No verifier is enabled by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from typing import Callable, Mapping
from urllib.parse import urlsplit
import uuid


SPORTS = frozenset({"at", "basket", "futbol", "hisse"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+ -]{0,127}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,95}\Z")
_APPLICATION_ID = 0x544A4B43
_SCHEMA_VERSION = 1


class SafeStoreError(Exception):
    """A fixed code safe to report without exposing paths or evidence payloads."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedResult:
    sport: str
    event_id: str
    market: str
    closes_at: datetime
    status: str
    winner: str | None
    final_at: datetime
    source_id: str
    source_url: str
    verified_at: datetime


def _stamp(value: datetime, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeStoreError(code)
    try:
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise SafeStoreError(code) from None


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SafeStoreError(code)
    return value


def _scope(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"-[0-9]{1,20}", value):
        return value
    return _identifier(value, "INVALID_SCOPE")


def _url(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise SafeStoreError(code)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            raise ValueError
    except ValueError:
        raise SafeStoreError(code) from None
    return value


def _provenance(values: tuple[dict, ...], now: str) -> str:
    if not isinstance(values, (tuple, list)) or len(values) > 32:
        raise SafeStoreError("INVALID_PROVENANCE")
    result = []
    allowed = {"source_id", "source_url", "as_of", "name", "delay_seconds"}
    for item in values:
        if (not isinstance(item, dict) or not set(item) <= allowed
                or not {"source_id", "source_url", "as_of"} <= set(item)):
            raise SafeStoreError("INVALID_PROVENANCE")
        source_time = item["as_of"]
        if isinstance(source_time, str):
            try:
                source_time = datetime.fromisoformat(source_time)
            except ValueError:
                raise SafeStoreError("INVALID_PROVENANCE") from None
        row = {
            "source_id": _identifier(item["source_id"], "INVALID_PROVENANCE"),
            "source_url": _url(item["source_url"], "INVALID_PROVENANCE"),
            "as_of": _stamp(source_time, "INVALID_PROVENANCE"),
        }
        if row["as_of"] > now:
            raise SafeStoreError("INVALID_PROVENANCE")
        if "name" in item:
            row["name"] = _identifier(item["name"], "INVALID_PROVENANCE")
        if "delay_seconds" in item:
            delay = item["delay_seconds"]
            if type(delay) is not int or not 0 <= delay <= 31_536_000:
                raise SafeStoreError("INVALID_PROVENANCE")
            row["delay_seconds"] = delay
        result.append(row)
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _private_path(path: str | os.PathLike) -> tuple[Path, bool]:
    """Create only new private directories/files; never chmod existing content."""
    raw = Path(path)
    if ".." in raw.parts or not raw.name:
        raise SafeStoreError("UNSAFE_DATABASE_PATH")
    absolute = Path(os.path.abspath(raw))
    current = Path(absolute.anchor)
    for part in absolute.parent.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SafeStoreError("UNSAFE_DATABASE_PATH")
    parent = absolute.parent.lstat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise SafeStoreError("UNSAFE_DATABASE_PATH")
    created = False
    try:
        fd = os.open(absolute, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
        created = True
    info = absolute.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise SafeStoreError("UNSAFE_DATABASE_PATH")
    return absolute, created


_SCHEMA = """
CREATE TABLE predictions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    sport TEXT NOT NULL CHECK(sport IN ('at','basket','futbol','hisse')),
    model TEXT NOT NULL,
    event_id TEXT,
    market TEXT,
    closes_at TEXT,
    selection TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('PAS','PREDICTION')),
    reasons_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(scope, request_id),
    CHECK(decision = 'PAS' OR (event_id IS NOT NULL AND market IS NOT NULL
          AND closes_at > created_at AND selection IS NOT NULL))
);
CREATE TABLE verified_results (
    id TEXT PRIMARY KEY,
    sport TEXT NOT NULL CHECK(sport IN ('at','basket','futbol','hisse')),
    event_id TEXT NOT NULL,
    market TEXT NOT NULL,
    closes_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('FINAL','VOID')),
    winner TEXT,
    final_at TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(sport, event_id, market, closes_at),
    CHECK((status='VOID' OR final_at >= closes_at) AND verified_at >= final_at AND recorded_at >= verified_at),
    CHECK((status = 'FINAL' AND winner IS NOT NULL) OR (status = 'VOID' AND winner IS NULL))
);
CREATE INDEX prediction_scope_model ON predictions(scope, sport, model);
CREATE UNIQUE INDEX prediction_identity ON predictions(scope,sport,model,event_id,market,closes_at)
WHERE decision='PREDICTION';
CREATE TABLE command_receipts (
    scope TEXT NOT NULL,
    request_id TEXT NOT NULL,
    prediction_id TEXT NOT NULL REFERENCES predictions(id),
    received_at TEXT NOT NULL,
    PRIMARY KEY(scope,request_id)
);
CREATE TRIGGER predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER results_no_update BEFORE UPDATE ON verified_results
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER results_no_delete BEFORE DELETE ON verified_results
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER receipts_no_update BEFORE UPDATE ON command_receipts
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER receipts_no_delete BEFORE DELETE ON command_receipts
BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
"""


class PredictionStore:
    def __init__(self, path: str | os.PathLike, *, verifiers: Mapping[str, Callable] | None = None,
                 clock: Callable[[], datetime] | None = None):
        self._lock = threading.RLock()
        self._connection = None
        self._verifiers = dict(verifiers or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if not callable(self._clock):
            raise SafeStoreError("INVALID_CLOCK_CONFIGURATION")
        for name, callback in self._verifiers.items():
            _identifier(name, "INVALID_VERIFIER_CONFIGURATION")
            if not callable(callback):
                raise SafeStoreError("INVALID_VERIFIER_CONFIGURATION")
        try:
            private_path, created = _private_path(path)
            connection = sqlite3.connect(private_path.as_uri() + "?mode=rw", uri=True,
                                         timeout=5, isolation_level=None, check_same_thread=False)
            self._connection = connection
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA busy_timeout=5000")
            if created:
                connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA
                                         + f"PRAGMA application_id={_APPLICATION_ID};\n"
                                         + f"PRAGMA user_version={_SCHEMA_VERSION};\nCOMMIT;")
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                  or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION):
                raise SafeStoreError("UNRELATED_DATABASE")
            expected = {"predictions", "verified_results", "command_receipts"}
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != expected:
                raise SafeStoreError("INVALID_DATABASE_SCHEMA")
        except SafeStoreError:
            self.close()
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError):
            self.close()
            raise SafeStoreError("DATABASE_UNAVAILABLE") from None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _db(self):
        if self._connection is None:
            raise SafeStoreError("DATABASE_CLOSED")
        return self._connection

    @staticmethod
    def _prediction(row, *, reused=False):
        value = dict(row)
        value["reasons"] = tuple(json.loads(value.pop("reasons_json")))
        value["provenance"] = tuple(json.loads(value.pop("provenance_json")))
        value["reused"] = reused
        return value

    def record_prediction(self, *, request_id: str, scope: str, sport: str, model: str,
                          event_id: str | None, market: str | None,
                          closes_at: datetime | None, selection: str | None,
                          reasons: tuple[str, ...], now: datetime,
                          provenance: tuple[dict, ...] = ()) -> dict:
        request_id = _identifier(request_id, "INVALID_PREDICTION")
        scope = _scope(scope)
        with self._lock:
            db = self._db()
            try:
                db.execute("BEGIN IMMEDIATE")
                original = db.execute("SELECT p.* FROM command_receipts c JOIN predictions p "
                                      "ON p.id=c.prediction_id WHERE c.scope=? AND c.request_id=?",
                                      (scope, request_id)).fetchone()
                if original is not None:
                    db.execute("COMMIT")
                    return self._prediction(original, reused=True)
                if sport not in SPORTS:
                    raise SafeStoreError("INVALID_PREDICTION")
                model = _identifier(model, "INVALID_PREDICTION")
                created_at = _stamp(now, "INVALID_PREDICTION")
                cutoff = None if closes_at is None else _stamp(closes_at, "INVALID_PREDICTION")
                for value in (event_id, market, selection):
                    if value is not None:
                        _identifier(value, "INVALID_PREDICTION")
                if (not isinstance(reasons, (tuple, list)) or len(reasons) > 32
                        or any(not isinstance(r, str) or not _REASON.fullmatch(r) for r in reasons)):
                    raise SafeStoreError("INVALID_PREDICTION")
                reason_list = list(dict.fromkeys(reasons))
                if any(value is None for value in (event_id, market, cutoff, selection)):
                    reason_list.append("MISSING_PREDICTION_DATA")
                if cutoff is not None and cutoff <= created_at:
                    reason_list.append("PREDICTION_CLOSED")
                if all(value is not None for value in (event_id, market, cutoff)):
                    if db.execute("SELECT 1 FROM verified_results WHERE sport=? AND event_id=? "
                                  "AND market=? AND closes_at=?", (sport, event_id, market, cutoff)).fetchone():
                        reason_list.append("RESULT_ALREADY_RECORDED")
                decision = "PAS" if reason_list else "PREDICTION"
                metadata = _provenance(provenance, created_at)
                if decision == "PREDICTION":
                    original = db.execute("SELECT * FROM predictions WHERE scope=? AND sport=? "
                        "AND model=? AND event_id=? AND market=? AND closes_at=? AND decision='PREDICTION'",
                        (scope, sport, model, event_id, market, cutoff)).fetchone()
                    if original is not None:
                        db.execute("INSERT INTO command_receipts VALUES (?,?,?,?)",
                                   (scope, request_id, original["id"], created_at))
                        db.execute("COMMIT")
                        return self._prediction(original, reused=True)
                identifier = uuid.uuid4().hex
                db.execute("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    identifier, request_id, scope, sport, model, event_id, market, cutoff,
                    selection if decision == "PREDICTION" else None, decision,
                    json.dumps(list(dict.fromkeys(reason_list))), metadata, created_at))
                db.execute("INSERT INTO command_receipts VALUES (?,?,?,?)",
                           (scope, request_id, identifier, created_at))
                row = db.execute("SELECT * FROM predictions WHERE id=?", (identifier,)).fetchone()
                db.execute("COMMIT")
                return self._prediction(row)
            except SafeStoreError:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            except (sqlite3.Error, ValueError, TypeError):
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise SafeStoreError("PREDICTION_WRITE_FAILED") from None

    def record_result(self, verifier_id: str, payload: object, *, now: datetime | None = None) -> dict:
        verifier_id = _identifier(verifier_id, "RESULT_VERIFIER_UNAVAILABLE")
        verifier = self._verifiers.get(verifier_id)
        if verifier is None:
            raise SafeStoreError("RESULT_VERIFIER_UNAVAILABLE")
        try:
            verified = verifier(payload)
        except Exception:
            raise SafeStoreError("RESULT_VERIFICATION_FAILED") from None
        if not isinstance(verified, VerifiedResult):
            raise SafeStoreError("RESULT_VERIFICATION_FAILED")
        try:
            recorded_at = _stamp(now if now is not None else self._clock(), "INVALID_RESULT")
        except Exception:
            raise SafeStoreError("INVALID_RESULT") from None
        if (not isinstance(verified.sport, str) or verified.sport not in SPORTS or verified.source_id != verifier_id
                or verified.status not in ("FINAL", "VOID")):
            raise SafeStoreError("INVALID_RESULT")
        event_id = _identifier(verified.event_id, "INVALID_RESULT")
        market = _identifier(verified.market, "INVALID_RESULT")
        if verified.status == "FINAL":
            _identifier(verified.winner, "INVALID_RESULT")
        elif verified.winner is not None:
            raise SafeStoreError("INVALID_RESULT")
        cutoff = _stamp(verified.closes_at, "INVALID_RESULT")
        final_at = _stamp(verified.final_at, "INVALID_RESULT")
        verified_at = _stamp(verified.verified_at, "INVALID_RESULT")
        if not final_at <= verified_at <= recorded_at or (verified.status == "FINAL" and cutoff > final_at):
            raise SafeStoreError("INVALID_RESULT")
        source_url = _url(verified.source_url, "INVALID_RESULT")
        values = (verified.sport, event_id, market, cutoff, verified.status, verified.winner,
                  final_at, verifier_id, source_url, verified_at)
        with self._lock:
            db = self._db()
            try:
                db.execute("BEGIN IMMEDIATE")
                original = db.execute("SELECT * FROM verified_results WHERE sport=? AND event_id=? "
                                      "AND market=? AND closes_at=?", values[:4]).fetchone()
                if original is not None:
                    fields = ("sport", "event_id", "market", "closes_at", "status", "winner", "final_at",
                              "source_id", "source_url")
                    if tuple(original[field] for field in fields) != values[:-1]:
                        raise SafeStoreError("RESULT_CONFLICT")
                    db.execute("COMMIT")
                    return dict(original)
                identifier = uuid.uuid4().hex
                db.execute("INSERT INTO verified_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (identifier, *values, recorded_at))
                row = db.execute("SELECT * FROM verified_results WHERE id=?", (identifier,)).fetchone()
                db.execute("COMMIT")
                return dict(row)
            except SafeStoreError:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            except sqlite3.Error:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise SafeStoreError("RESULT_WRITE_FAILED") from None

    def stats(self, scope: str) -> list[dict]:
        scope = _scope(scope)
        with self._lock:
            try:
                rows = self._db().execute("""
                    SELECT p.sport, p.model, COUNT(*) AS total,
                      SUM(p.decision='PAS') AS pas,
                      SUM(p.decision='PREDICTION' AND r.id IS NULL) AS pending,
                      SUM(p.decision='PREDICTION' AND r.status='FINAL' AND p.selection=r.winner) AS correct,
                      SUM(p.decision='PREDICTION' AND r.status='FINAL' AND p.selection<>r.winner) AS incorrect,
                      SUM(p.decision='PREDICTION' AND r.status='VOID') AS void
                    FROM predictions p LEFT JOIN verified_results r
                      ON p.sport=r.sport AND p.event_id=r.event_id AND p.market=r.market
                      AND p.closes_at=r.closes_at
                    WHERE p.scope=? GROUP BY p.sport,p.model ORDER BY p.sport,p.model
                """, (scope,)).fetchall()
                result = []
                for row in rows:
                    item = dict(row)
                    for key in ("total", "pas", "pending", "correct", "incorrect", "void"):
                        item[key] = item[key] or 0
                    settled = item["correct"] + item["incorrect"]
                    item["success_rate"] = item["correct"] / settled if settled else None
                    result.append(item)
                return result
            except sqlite3.Error:
                raise SafeStoreError("STATISTICS_UNAVAILABLE") from None
