"""Authenticated, bounded analysis API. No file paths, shell, or order endpoints."""
from __future__ import annotations

import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import sys
import threading

from .auth import Authenticator, AuthError, response_signature
from .jobs import JobError, Jobs
from .storage import MAX_JSON_BYTES, Store, StoreError, decode_json, read_private_key

SOCKET_TIMEOUT = 3.0
CONNECTION_DEADLINE = 8.0
MAX_HANDLERS = 4
MAX_HEADER_BYTES = 16384
TAILNET = ipaddress.ip_network("100.64.0.0/10")
OBJECT_ROUTE = re.compile(r"/v1/(inputs|jobs)/([0-9a-f]{32})\Z")


def peer_allowed(address: str) -> bool:
    """Trust the socket peer, never forwarding headers or an arbitrary LAN host."""
    try:
        value = ipaddress.ip_address(address)
        return isinstance(value, ipaddress.IPv4Address) and (value.is_loopback or value in TAILNET)
    except ValueError:
        return False


def validate_bind(host: str, tailnet_bind: bool = False):
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        raise ValueError("bind_requires_ipv4_literal") from None
    if address.is_loopback:
        return
    if tailnet_bind and (address in TAILNET or address.is_unspecified):
        return
    raise ValueError("bind_requires_loopback_or_explicit_tailnet")


class _HeaderReader:
    """Limit total request line + headers while leaving body reads bounded separately."""
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_HEADER_BYTES

    def readline(self, limit=-1):
        maximum = self.remaining + 1
        if limit >= 0:
            maximum = min(maximum, limit)
        data = self.stream.readline(maximum)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise http.client.LineTooLong("headers")
        return data

    def read(self, length):
        return self.stream.read(length)

    def close(self):
        self.stream.close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "TermuxAnalysis"
    sys_version = ""

    def setup(self):
        super().setup()
        self.rfile = _HeaderReader(self.rfile)
        self.verified_nonce = None

    def log_message(self, *_args):
        pass  # Never log payloads, URLs, headers, credentials, or account data.

    def send_error(self, *_args, **_kwargs):
        self._reply(401, {"error": "authentication_required"})

    def _reply(self, status, payload):
        payload = {**payload, "execution_enabled": False}
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(body) > MAX_JSON_BYTES:
            # Stored JSON plus response metadata must still fit the client's cap.
            status = 413
            body = b'{"error":"response_too_large","execution_enabled":false}'
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.verified_nonce is not None:
            self.send_header("X-TM-Response-Signature", response_signature(
                self.server.key, self.verified_nonce, status, body))
        self.end_headers()
        self.wfile.write(body)

    def handle_one_request(self):
        # One request only. Even a signed keep-alive/pipeline cannot reuse a socket.
        self.close_connection = True
        try:
            self.raw_requestline = self.rfile.readline(2049)
            if not self.raw_requestline:
                return
            if len(self.raw_requestline) > 2048:
                self.request_version = "HTTP/1.0"
                self._reply(401, {"error": "authentication_required"})
                return
            if not self.parse_request():
                return
            self.close_connection = True
            # BaseHTTPRequestHandler normalizes leading //; authenticate the exact target.
            self.path = self.raw_requestline.decode("iso-8859-1").split()[1]
            self._dispatch()
        except (OSError, TimeoutError, http.client.HTTPException):
            pass
        except Exception:
            try:
                self._reply(500 if self.verified_nonce else 401,
                            {"error": "internal_error" if self.verified_nonce else "authentication_required"})
            except (OSError, ValueError):
                pass
        finally:
            self.close_connection = True

    def _dispatch(self):
        headers = {}
        for name, value in self.headers.raw_items():
            lower = name.lower()
            if lower in headers:
                # Ambiguous headers (including auth, Host and Content-Length) fail closed.
                self._reply(401, {"error": "authentication_required"})
                return
            headers[lower] = value
        if any(name in headers for name in ("transfer-encoding", "content-encoding", "expect")):
            self._reply(401, {"error": "authentication_required"})
            return
        length_text = headers.get("content-length", "0")
        if re.fullmatch(r"[0-9]{1,9}", length_text) is None:
            self._reply(401, {"error": "authentication_required"})
            return
        length = int(length_text)
        if length > MAX_JSON_BYTES:
            self._reply(401, {"error": "authentication_required"})
            return
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            self._reply(401, {"error": "authentication_required"})
            return
        try:
            self.verified_nonce = self.server.auth.verify(self.command, self.path, headers, body)
        except AuthError:
            self._reply(401, {"error": "authentication_required"})
            return
        if "origin" in headers:
            self._reply(403, {"error": "browser_origin_not_allowed"})
            return
        if (any(char in self.path for char in "%\\?#") or "//" in self.path
                or any(part in {".", ".."} for part in self.path.split("/"))
                or any(ord(char) < 32 or ord(char) >= 127 for char in self.path)):
            self._reply(400, {"error": "invalid_path"})
            return
        if self.command not in {"GET", "POST"}:
            self._reply(405, {"error": "method_not_allowed"})
            return
        if self.command == "GET" and body:
            self._reply(400, {"error": "get_body_not_allowed"})
            return
        payload = None
        if self.command == "POST":
            if "content-length" not in headers:
                self._reply(411, {"error": "content_length_required"})
                return
            if headers.get("content-type", "").lower() not in {"application/json", "application/json; charset=utf-8"}:
                self._reply(415, {"error": "json_required"})
                return
            try:
                payload = decode_json(body)
            except StoreError:
                self._reply(400, {"error": "invalid_json"})
                return
        try:
            self._route(payload)
        except StoreError as error:
            status = {"not_found": 404, "already_exists": 409,
                      "object_quota": 507, "object_too_large": 413,
                      "invalid_id": 400}.get(error.code, 503)
            code = error.code if status != 503 else "storage_unavailable"
            self._reply(status, {"error": code})
        except JobError as error:
            status = {"job_in_progress": 409, "job_rate_limit": 429,
                      "request_id_conflict": 409}.get(error.code, 400)
            self._reply(status, {"error": error.code})

    def _route(self, payload):
        if self.path == "/v1/health" and self.command == "GET":
            self._reply(200, {"ok": True})
        elif self.path == "/v1/status" and self.command == "GET":
            with self.server.jobs.lock:
                self._reply(200, {"counts": self.server.store.counts(),
                                  "current_job": self.server.jobs.current})
        elif self.path == "/v1/inputs" and self.command == "POST":
            if set(payload) != {"context"} or type(payload["context"]) is not dict:
                self._reply(400, {"error": "invalid_input"})
                return
            self._reply(201, self.server.jobs.upload(payload["context"]))
        elif self.path == "/v1/jobs" and self.command == "POST":
            self._reply(202, self.server.jobs.submit(payload))
        else:
            match = OBJECT_ROUTE.fullmatch(self.path)
            if match and self.command == "GET":
                namespace, object_id = match.groups()
                if namespace == "inputs":
                    self._reply(200, {"id": object_id, "context": self.server.store.read(namespace, object_id)})
                else:
                    self._reply(200, self.server.jobs.status(object_id))
            else:
                self._reply(404, {"error": "not_found"})


class ManagementServer(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET
    request_queue_size = 8
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, key, project_id, store, jobs):
        self.key, self.store, self.jobs = key, store, jobs
        self.auth = Authenticator(key, project_id, store)
        self._slots = threading.BoundedSemaphore(MAX_HANDLERS)
        self._lifetime_lock = None
        self._closing = False
        # Independent descriptor: a second manager cannot share one Store's lock.
        fd = os.open(".server.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=store._root_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise ValueError("unsafe_server_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("manager_already_running") from None
            self._lifetime_lock = fd
            super().__init__(address, _Handler)
        except Exception:
            if self._lifetime_lock is not None:
                self._lifetime_lock = None
                os.close(fd)
            else:
                # Before ownership transfer, the local descriptor remains ours.
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

    def server_bind(self):
        # Avoid reverse DNS; only numeric IPv4 binds are accepted.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "termux-manager"
        self.server_port = self.server_address[1]

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(SOCKET_TIMEOUT)
        return connection, address

    def verify_request(self, request, client_address):
        return peer_allowed(client_address[0])

    @staticmethod
    def _expire(request):
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def process_request(self, request, client_address):
        if self._closing or not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        deadline = threading.Timer(CONNECTION_DEADLINE, self._expire, args=(request,))
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self._slots.release()

    def handle_error(self, *_args):
        pass

    def server_close(self):
        self._closing = True
        super().server_close()
        if self._lifetime_lock is not None:
            # Keep the runtime lock until the analysis worker has really exited.
            self.jobs.close()
            worker = self.jobs.worker
            if worker is not None and worker is not threading.current_thread():
                worker.join()
            os.close(self._lifetime_lock)
            self._lifetime_lock = None


def make_server(host, port, key, project_id, store, jobs=None, tailnet_bind=False):
    """Caller owns Store lifetime; close this server before closing Store."""
    validate_bind(host, tailnet_bind)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid_port")
    return ManagementServer((host, port), key, project_id, store,
                            jobs if jobs is not None else Jobs(store))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private analysis API; execution is always disabled")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--tailnet-bind", action="store_true")
    args = parser.parse_args(argv)
    store = server = None
    try:
        validate_bind(args.bind, args.tailnet_bind)
        key = read_private_key(args.key_file)
        store = Store(args.root)
        server = make_server(args.bind, args.port, key, args.project_id, store,
                             tailnet_bind=args.tailnet_bind)
        print(f"Analysis API listening on {args.bind}:{server.server_port}; execution_enabled=false", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 0
    except (OSError, StoreError, ValueError):
        print("Manager could not start; verify private project configuration and bind availability.", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
