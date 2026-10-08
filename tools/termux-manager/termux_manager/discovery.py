"""Read-only discovery of this UID's standard python -m http.server document roots.

No process is stopped, signalled, started, or modified. Custom servers cannot be
identified reliably and require explicit --public-root instead.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time


class DiscoveryError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise DiscoveryError("unsupported_http_server_arguments")


def _read(path, maximum):
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise DiscoveryError("process_metadata_too_large")
    return value


def discover_http_roots(port: int, proc_root: Path = Path("/proc")) -> list[Path]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise DiscoveryError("invalid_http_port")
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("-b", "--bind")
    parser.add_argument("-d", "--directory")
    parser.add_argument("-p", "--protocol")
    parser.add_argument("--cgi", action="store_true")
    roots = set()
    deadline = time.monotonic() + 5
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        raise DiscoveryError("process_metadata_unavailable_use_public_root") from None
    if len(processes) > 10000:
        raise DiscoveryError("process_discovery_limit")
    for process in processes:
        if time.monotonic() > deadline:
            raise DiscoveryError("process_discovery_limit")
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            name = _read(process / "comm", 128).decode("ascii", errors="replace").strip()
            if not name.startswith("python"):
                continue
            raw = _read(process / "cmdline", 16384)
            args = [os.fsdecode(part) for part in raw.split(b"\0") if part]
        except (FileNotFoundError, ProcessLookupError):
            continue  # A process that exited cannot still serve the key.
        except PermissionError:
            # Android may hide process metadata. Do not claim discovery succeeded.
            raise DiscoveryError("process_metadata_unavailable_use_public_root") from None
        except OSError:
            raise DiscoveryError("process_metadata_unavailable_use_public_root") from None
        if "-m" not in args:
            continue
        marker = args.index("-m")
        if any(flag not in {"-B", "-E", "-I", "-s", "-S", "-u", "-O", "-OO"} for flag in args[1:marker]):
            continue  # A script/-c argument containing '-m http.server' is not proof.
        if marker + 1 >= len(args) or args[marker + 1] != "http.server":
            continue
        options = parser.parse_args(args[marker + 2:])
        if options.port != port:
            continue
        try:
            if options.directory is None or not Path(options.directory).is_absolute():
                working = Path(os.readlink(process / "cwd"))
                if not working.is_absolute():
                    raise DiscoveryError("http_working_directory_unverified")
                document_root = working / (options.directory or ".")
            else:
                document_root = Path(options.directory)
            document_root = document_root.resolve(strict=True)
            if not document_root.is_dir():
                raise DiscoveryError("http_document_root_unverified")
            # Detect obvious process exit/reuse or command replacement while read.
            if _read(process / "cmdline", 16384) != raw:
                raise DiscoveryError("http_process_changed_during_discovery")
            roots.add(document_root)
        except (OSError, RuntimeError):
            raise DiscoveryError("http_document_root_unavailable_use_public_root") from None
    if not roots:
        raise DiscoveryError("standard_http_server_not_found_use_public_root")
    return sorted(roots)
