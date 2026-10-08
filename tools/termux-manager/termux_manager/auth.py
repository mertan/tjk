"""Project-scoped request/response HMACs and a durable replay ledger."""

from __future__ import annotations

import hashlib
import hmac
import re
import time

from .storage import StoreError, read_private_key

_ID = re.compile(r"[0-9a-f]{32}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"[0-9]{1,12}\Z")
CLOCK_WINDOW_SECONDS = 30


class AuthError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identity(key: bytes, project_id: str):
    if (not isinstance(key, bytes) or len(key) != 32
            or not isinstance(project_id, str) or not _ID.fullmatch(project_id)):
        raise ValueError("invalid authentication configuration")


def sign_request(key: bytes, project_id: str, method: str, path: str,
                 timestamp: str, nonce: str, body: bytes) -> str:
    _identity(key, project_id)
    if (not isinstance(method, str) or re.fullmatch(r"[A-Z]+", method.upper()) is None
            or not isinstance(path, str) or not path.startswith("/")
            or "\n" in path or "\r" in path
            or not isinstance(timestamp, str) or not _TIMESTAMP.fullmatch(timestamp)
            or not isinstance(nonce, str) or not _ID.fullmatch(nonce)
            or not isinstance(body, bytes)):
        raise ValueError("invalid signing input")
    canonical = (f"TM1\n{project_id}\n{method.upper()}\n{path}\n{timestamp}\n"
                 f"{nonce}\n{hashlib.sha256(body).hexdigest()}").encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def response_signature(key: bytes, nonce: str, status: int, body: bytes) -> str:
    if (not isinstance(key, bytes) or len(key) != 32
            or not isinstance(nonce, str) or not _ID.fullmatch(nonce)
            or type(status) is not int or not 100 <= status <= 599
            or not isinstance(body, bytes)):
        raise ValueError("invalid response signing input")
    canonical = (f"TM1-RESPONSE\n{nonce}\n{status}\n"
                 f"{hashlib.sha256(body).hexdigest()}").encode("ascii")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


class Authenticator:
    def __init__(self, key: bytes, project_id: str, store):
        _identity(key, project_id)
        self._key = key
        self.project_id = project_id
        self._store = store

    def verify(self, method: str, path: str, headers: dict[str, str], body: bytes,
               now: int | None = None) -> str:
        normalized = {}
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise AuthError("invalid_auth")
            lower = name.lower()
            if lower in normalized:
                raise AuthError("invalid_auth")
            normalized[lower] = value
        project = normalized.get("x-tm-project", "")
        timestamp = normalized.get("x-tm-time", "")
        nonce = normalized.get("x-tm-nonce", "")
        signature = normalized.get("x-tm-signature", "")
        if (not _ID.fullmatch(project) or not _TIMESTAMP.fullmatch(timestamp)
                or not _ID.fullmatch(nonce) or not _SIGNATURE.fullmatch(signature)
                or not hmac.compare_digest(project, self.project_id)):
            raise AuthError("invalid_auth")
        accepted_at = int(time.time()) if now is None else now
        if type(accepted_at) is not int or abs(accepted_at - int(timestamp)) > CLOCK_WINDOW_SECONDS:
            raise AuthError("expired_auth")
        try:
            expected = sign_request(self._key, self.project_id, method, path,
                                    timestamp, nonce, body)
        except (ValueError, UnicodeError) as exc:
            raise AuthError("invalid_auth") from exc
        if not hmac.compare_digest(signature, expected):
            raise AuthError("invalid_auth")
        try:
            if not self._store.reserve_nonce(nonce, accepted_at):
                raise AuthError("replayed_auth")
        except StoreError as exc:
            raise AuthError("auth_unavailable") from exc
        return nonce
