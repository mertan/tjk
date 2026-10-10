"""Fixed-route management client; authenticates requests and responses.

No credential is transmitted. HTTP is appropriate only inside the user's
existing Tailscale network: HMAC provides authenticity, not encryption.
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .auth import read_private_key, response_signature, sign_request
from .storage import StoreError

MAX_BODY = 128 * 1024
TIMEOUT = 20
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


class ClientError(ValueError):
    """A safely reportable failure, without request bodies or credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_url(value: str) -> tuple[str, bool]:
    """Return normalized base URL and whether it is an IPv4 loopback."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ClientError("Invalid management URL")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ClientError("Management URL contains whitespace or controls")
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ClientError("Invalid management URL") from None
    if (parts.scheme not in {"http", "https"} or not host or
            parts.username is not None or parts.password is not None or
            parts.path not in {"", "/"} or parts.query or parts.fragment or
            "?" in value or "#" in value):
        raise ClientError("URL must be an HTTP(S) origin without credentials or a path")
    if port is not None and not (1 <= port <= 65535):
        raise ClientError("Invalid management port")
    loopback = False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if (len(host) > 253 or not host.endswith(".ts.net") or
                not all(DNS_LABEL.fullmatch(label) for label in host.split("."))):
            raise ClientError("Only Tailscale DNS names or approved IPv4 addresses are allowed") from None
    else:
        if not isinstance(addr, ipaddress.IPv4Address):
            raise ClientError("Only IPv4 loopback or Tailscale addresses are allowed")
        loopback = addr.is_loopback
        if not loopback and addr not in TAILNET:
            raise ClientError("Address is outside loopback and the Tailscale IPv4 range")
    suffix = f":{port}" if port is not None else ""
    return f"{parts.scheme}://{host}{suffix}", loopback


def valid_id(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ClientError("Identifiers must contain exactly 32 lowercase hexadecimal characters")
    return value


def _json_bytes(value: dict) -> bytes:
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ClientError("Request must be finite JSON") from None
    if len(encoded) > MAX_BODY:
        raise ClientError("Request exceeds 128 KiB")
    return encoded


def _reject_constant(value):
    raise ValueError("Non-finite JSON")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def decode_json(data: bytes):
    return json.loads(data.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)


class Client:
    def __init__(self, url: str, key: bytes, project_id: str, *, bypass_proxy: bool = False):
        self.url, loopback = validate_url(url)
        if not isinstance(key, bytes) or len(key) != 32:
            raise ClientError("Invalid authentication key")
        self.key = key
        self.project_id = valid_id(project_id)
        if bypass_proxy and not loopback:
            raise ClientError("Proxy bypass is permitted only for loopback tests")
        handlers = [NoRedirect()]
        if bypass_proxy:
            handlers.append(ProxyHandler({}))
        self.opener = build_opener(*handlers)

    def _request(self, method: str, path: str, payload: dict | None = None):
        allowed_get = path in {"/v1/health", "/v1/status"} or re.fullmatch(r"/v1/(?:inputs|jobs)/[0-9a-f]{32}", path)
        if (method == "GET" and allowed_get and payload is None):
            body = b""
        elif method == "POST" and path in {"/v1/inputs", "/v1/jobs"} and isinstance(payload, dict):
            body = _json_bytes(payload)
        else:
            raise ClientError("Unsupported management operation")
        nonce = secrets.token_hex(16)
        timestamp = str(int(time.time()))
        headers = {
            "X-TM-Project": self.project_id,
            "X-TM-Time": timestamp,
            "X-TM-Nonce": nonce,
            "X-TM-Signature": sign_request(self.key, self.project_id, method, path, timestamp, nonce, body),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "termux-manager/1",
        }
        if method == "POST":
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=body if method == "POST" else None, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=TIMEOUT)
            except HTTPError as error:
                # HTTPError is a response. Authenticate it before using its body.
                response = error
            with response:
                status = response.status
                data = response.read(MAX_BODY + 1)
                received_signature = response.headers.get("X-TM-Response-Signature", "")
                if len(data) > MAX_BODY:
                    raise ClientError("Response exceeds 128 KiB")
                expected_signature = response_signature(self.key, nonce, status, data)
                if (not isinstance(received_signature, str) or
                        not re.fullmatch(r"[0-9a-f]{64}", received_signature) or
                        not hmac.compare_digest(received_signature, expected_signature)):
                    raise ClientError("Response authentication failed")
                if status < 200 or status >= 300:
                    raise ClientError(f"Authenticated request failed (HTTP {status})")
                try:
                    decoded = decode_json(data)
                except (ValueError, UnicodeError, RecursionError):
                    raise ClientError("Authenticated response is invalid JSON") from None
                if not isinstance(decoded, dict):
                    raise ClientError("Authenticated response must be a JSON object")
                return decoded
        except ClientError:
            raise
        except (OSError, URLError, TimeoutError, http.client.HTTPException):
            raise ClientError("Connection failed; no operation result was confirmed") from None

    def health(self):
        return self._request("GET", "/v1/health")

    def status(self):
        return self._request("GET", "/v1/status")

    def upload(self, context: dict):
        if not isinstance(context, dict):
            raise ClientError("Input context must be a JSON object")
        return self._request("POST", "/v1/inputs", {"context": context})

    def get_input(self, input_id: str):
        return self._request("GET", f"/v1/inputs/{valid_id(input_id)}")

    def scan(self, input_id: str, request_id: str):
        return self._request("POST", "/v1/jobs", {
            "action": "scan", "input_id": valid_id(input_id), "request_id": valid_id(request_id),
        })

    def job(self, job_id: str):
        return self._request("GET", f"/v1/jobs/{valid_id(job_id)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Authenticated analysis-only Termux manager client")
    parser.add_argument("--url", required=True)
    parser.add_argument("--key-file", required=True, type=Path)
    project = parser.add_mutually_exclusive_group(required=True)
    project.add_argument("--project-id")
    project.add_argument("--config", type=Path, help="Installed project.json containing public project_id")
    parser.add_argument("--bypass-proxy", action="store_true", help="Loopback testing only")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("status")
    upload = commands.add_parser("upload")
    upload.add_argument("--file", type=Path, required=True, help="JSON context object, without an extra context wrapper")
    scan = commands.add_parser("scan")
    scan.add_argument("--input-id", required=True)
    scan.add_argument("--request-id", help="Reuse the same ID when retrying an uncertain scan request")
    for command in ("job", "get-input"):
        commands.add_parser(command).add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        project_id = args.project_id
        if args.config:
            with args.config.open("rb") as stream:
                config = stream.read(4097)
            if len(config) > 4096:
                raise ClientError("Project configuration exceeds size limit")
            project_id = decode_json(config).get("project_id")
        client = Client(args.url, read_private_key(args.key_file), project_id, bypass_proxy=args.bypass_proxy)
        if args.command == "health":
            result = client.health()
        elif args.command == "status":
            result = client.status()
        elif args.command == "upload":
            with args.file.open("rb") as stream:
                data = stream.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                raise ClientError("Input exceeds 128 KiB")
            result = client.upload(decode_json(data))
        elif args.command == "scan":
            request_id = valid_id(args.request_id) if args.request_id else secrets.token_hex(16)
            # This public ID survives a lost response and enables idempotent retries.
            print(f"request_id={request_id}", file=sys.stderr, flush=True)
            result = client.scan(args.input_id, request_id)
        elif args.command == "job":
            result = client.job(args.id)
        else:
            result = client.get_input(args.id)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ClientError as error:
        print(f"Manager client: {error}", file=sys.stderr)
    except (ValueError, OSError, TypeError, AttributeError, RecursionError, StoreError):
        print("Manager client: invalid or inaccessible local configuration/input", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
