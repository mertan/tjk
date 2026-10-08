"""Bounded, create-only JSON storage anchored to private directory descriptors.

The installer creates the layout. This module never creates directories, accepts
filesystem paths from HTTP requests, follows symlinks, or replaces objects.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import threading

MAX_JSON_BYTES = 128 * 1024
MAX_OBJECTS = 64
MAX_NONCES = 4096
NONCE_RETENTION_SECONDS = 90
NAMESPACES = ("inputs", "jobs", "results")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Android's /data ancestors can be searchable without being readable by the
# Termux app UID. A traversal descriptor must not require directory-listing
# permission. O_DIRECTORY still rejects symlinks when O_PATH and O_NOFOLLOW are
# combined. Platforms without O_PATH keep the stricter, read-requiring fallback.
_TRAVERSE_FLAGS = (getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY
                   | os.O_NOFOLLOW | os.O_CLOEXEC)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class StoreError(Exception):
    """Public error code deliberately omits paths and object contents."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _valid_id(value: str) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _check_json_tree(value, depth=0):
    if depth > 64:
        raise StoreError("invalid_json")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        value.encode("utf-8")
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _check_json_tree(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise StoreError("invalid_json")
            key.encode("utf-8")
            _check_json_tree(item, depth + 1)
        return
    raise StoreError("invalid_json")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoreError("invalid_json")
        result[key] = value
    return result


def _reject_constant(_value):
    raise StoreError("invalid_json")


def decode_json(data: bytes) -> dict:
    """Decode an object without duplicate keys, nonfinite numbers, or deep trees."""
    if not isinstance(data, bytes) or len(data) > MAX_JSON_BYTES:
        raise StoreError("object_too_large")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
        if type(value) is not dict:
            raise StoreError("invalid_json")
        _check_json_tree(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StoreError("invalid_json") from exc


def _encode_json(value: dict) -> bytes:
    try:
        if type(value) is not dict:
            raise StoreError("invalid_json")
        _check_json_tree(value)
        data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StoreError("invalid_json") from exc
    if len(data) > MAX_JSON_BYTES:
        raise StoreError("object_too_large")
    return data


def _open_directory_path(path: Path) -> int:
    """Traverse nofollow without listing ancestors; return a readable final FD.

    Path.resolve would hide symlinks. O_PATH descriptors are only used while
    traversing: the returned descriptor must support listdir, flock and fsync.
    """
    absolute = Path(os.path.abspath(os.fspath(path)))
    if ".." in Path(path).parts:
        raise StoreError("unsafe_directory")
    components = absolute.parts[1:]
    fd = None
    try:
        fd = os.open("/", _TRAVERSE_FLAGS if components else _DIR_FLAGS)
        for index, component in enumerate(components):
            flags = _DIR_FLAGS if index == len(components) - 1 else _TRAVERSE_FLAGS
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise StoreError("unsafe_directory") from exc


def _private_directory(fd: int):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise StoreError("unsafe_directory")


def _private_file(fd: int, maximum: int):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or info.st_size > maximum):
        raise StoreError("unsafe_file")
    return info


def read_private_key(path: Path) -> bytes:
    """Read a private 0600, single-link file containing 32 bytes as lower hex."""
    path = Path(path)
    directory = _open_directory_path(path.parent)
    fd = None
    try:
        fd = os.open(path.name, _FILE_FLAGS, dir_fd=directory)
        _private_file(fd, 65)
        data = os.read(fd, 66)
        if data.endswith(b"\n"):
            data = data[:-1]
        if re.fullmatch(rb"[0-9a-f]{64}", data) is None:
            raise StoreError("invalid_key")
        return bytes.fromhex(data.decode("ascii"))
    except OSError as exc:
        raise StoreError("unsafe_file") from exc
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def read_private_json(path: Path) -> dict:
    """Read bounded JSON from an owned 0600 file without following symlinks."""
    path = Path(path)
    directory = _open_directory_path(path.parent)
    try:
        return decode_json(Store._read_bytes(directory, path.name, MAX_JSON_BYTES))
    finally:
        os.close(directory)


class Store:
    """An installed, private runtime root with bounded immutable JSON objects."""

    def __init__(self, root: Path):
        self._fds = {}
        self._mutex = threading.RLock()
        self._root_fd = None
        try:
            self._root_fd = _open_directory_path(Path(root))
            _private_directory(self._root_fd)
            for namespace in (*NAMESPACES, "nonces"):
                fd = os.open(namespace, _DIR_FLAGS, dir_fd=self._root_fd)
                self._fds[namespace] = fd
                _private_directory(fd)
        except (StoreError, OSError) as exc:
            self.close()
            if isinstance(exc, StoreError):
                raise
            raise StoreError("unsafe_directory") from exc

    def close(self):
        with self._mutex:
            for fd in self._fds.values():
                os.close(fd)
            self._fds.clear()
            if self._root_fd is not None:
                os.close(self._root_fd)
                self._root_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @contextlib.contextmanager
    def _locked(self, namespace: str):
        with self._mutex:
            if namespace not in self._fds or self._root_fd is None:
                raise StoreError("store_closed")
            fd = self._fds[namespace]
            try:
                _private_directory(self._root_fd)
                _private_directory(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield fd
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                raise StoreError("storage_unavailable") from exc

    @staticmethod
    def _name(namespace, object_id):
        if namespace not in NAMESPACES:
            raise StoreError("invalid_namespace")
        if not _valid_id(object_id):
            raise StoreError("invalid_id")
        return object_id + ".json"

    @staticmethod
    def _names(fd, suffix):
        names = os.listdir(fd)
        for name in names:
            if not name.endswith(suffix) or not _valid_id(name[:-len(suffix)]):
                raise StoreError("storage_corrupt")
        return names

    @staticmethod
    def _read_bytes(fd, name, maximum):
        item_fd = None
        try:
            item_fd = os.open(name, _FILE_FLAGS, dir_fd=fd)
            _private_file(item_fd, maximum)
            chunks = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(item_fd, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            result = b"".join(chunks)
            if len(result) > maximum:
                raise StoreError("object_too_large")
            _private_file(item_fd, maximum)
            return result
        except FileNotFoundError as exc:
            raise StoreError("not_found") from exc
        except OSError as exc:
            raise StoreError("unsafe_file") from exc
        finally:
            if item_fd is not None:
                os.close(item_fd)

    @staticmethod
    def _write_new(fd, name, data):
        item_fd = None
        try:
            item_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
            # A restrictive caller umask may remove owner bits; never relax
            # existing files, only this descriptor created with O_EXCL.
            os.fchmod(item_fd, 0o600)
            _private_file(item_fd, len(data))
            view = memoryview(data)
            while view:
                written = os.write(item_fd, view)
                if not written:
                    raise StoreError("storage_unavailable")
                view = view[written:]
            os.fsync(item_fd)
            _private_file(item_fd, len(data))
            os.fsync(fd)
        except FileExistsError as exc:
            raise StoreError("already_exists") from exc
        except OSError as exc:
            raise StoreError("storage_unavailable") from exc
        finally:
            if item_fd is not None:
                os.close(item_fd)
        # A failed write remains reserved, fail-closed. We never delete an
        # existing object or risk unlinking a concurrently substituted name.

    def create(self, namespace: str, object_id: str, value: dict) -> None:
        name = self._name(namespace, object_id)
        data = _encode_json(value)
        with self._locked(namespace) as fd:
            if len(self._names(fd, ".json")) >= MAX_OBJECTS:
                raise StoreError("object_quota")
            self._write_new(fd, name, data)

    def read(self, namespace: str, object_id: str) -> dict:
        name = self._name(namespace, object_id)
        with self._locked(namespace) as fd:
            return decode_json(self._read_bytes(fd, name, MAX_JSON_BYTES))

    def exists(self, namespace: str, object_id: str) -> bool:
        try:
            self.read(namespace, object_id)
            return True
        except StoreError as exc:
            if exc.code == "not_found":
                return False
            raise

    def counts(self) -> dict:
        result = {}
        for namespace in (*NAMESPACES, "nonces"):
            with self._locked(namespace) as fd:
                suffix = ".nonce" if namespace == "nonces" else ".json"
                names = self._names(fd, suffix)
                for name in names:
                    self._read_bytes(fd, name,
                                     32 if namespace == "nonces" else MAX_JSON_BYTES)
                result[namespace] = len(names)
        return result

    def reserve_nonce(self, nonce: str, now: int) -> bool:
        if not _valid_id(nonce) or type(now) is not int or not 0 <= now < 10**12:
            raise StoreError("invalid_nonce")
        with self._locked("nonces") as fd:
            names = self._names(fd, ".nonce")
            accepted_entries = []
            for name in names:
                data = self._read_bytes(fd, name, 32)
                if re.fullmatch(rb"[0-9]{1,12}\n", data) is None:
                    raise StoreError("storage_corrupt")
                accepted = int(data)
                # Once a forward clock jump has expired old nonces, a large
                # rollback must not revive their signed request timestamps.
                if accepted - now > 30:
                    raise StoreError("clock_rollback")
                accepted_entries.append((name, accepted))
            retained = 0
            for name, accepted in accepted_entries:
                if now - accepted > NONCE_RETENTION_SECONDS:
                    os.unlink(name, dir_fd=fd)
                else:
                    retained += 1
            # Persist cleanup as well as insertion before authenticating.
            os.fsync(fd)
            if retained >= MAX_NONCES:
                raise StoreError("nonce_quota")
            try:
                self._write_new(fd, nonce + ".nonce", f"{now}\n".encode("ascii"))
                return True
            except StoreError as exc:
                if exc.code == "already_exists":
                    return False
                raise
