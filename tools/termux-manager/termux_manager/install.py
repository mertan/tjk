"""Install into one newly created private directory; never start a service.

The operator must declare every existing HTTP document root. Same-user HTTP
servers can read mode-0600 secrets, so an install may not live below those roots.
This declaration cannot discover an undeclared server, a later server-root
change, or a web-root symlink that later points into the private project.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FILES = ("launch.py", "README.md", "context.example.json")
DIRECTORIES = ("termux_manager", "equity_guard", "docs", "tests")
MAX_PUBLIC_TREE_ENTRIES = 20_000
MAX_PUBLIC_TREE_DEPTH = 64
MAX_PUBLIC_TREE_SECONDS = 10


class InstallError(ValueError):
    """Installation failed without changing any pre-existing destination."""


def _preflight_runtime():
    if sys.version_info < (3, 11):
        raise InstallError("Python 3.11 or newer is required")
    try:
        ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        raise InstallError("America/New_York timezone data is required before installation") from None


def _source_manifest(source: Path) -> list[tuple[Path, bool]]:
    manifest = []
    if source.is_symlink() or not source.is_dir():
        raise InstallError("Source must be a real directory")
    for name in FILES:
        candidate = source / name
        try:
            mode = candidate.lstat().st_mode
        except OSError:
            raise InstallError(f"Required source file is missing: {name}") from None
        if not stat.S_ISREG(mode):
            raise InstallError(f"Source file must be regular and not a symlink: {name}")
        manifest.append((Path(name), False))
    for name in DIRECTORIES:
        directory = source / name
        try:
            mode = directory.lstat().st_mode
        except OSError:
            raise InstallError(f"Required source directory is missing: {name}") from None
        if not stat.S_ISDIR(mode):
            raise InstallError(f"Source directory must not be a symlink: {name}")
        manifest.append((Path(name), True))
        for current, directories, files in os.walk(directory, followlinks=False):
            directories[:] = sorted(item for item in directories if item != "__pycache__")
            for item in directories + sorted(files):
                candidate = Path(current) / item
                if item.endswith((".pyc", ".pyo")):
                    continue
                mode = candidate.lstat().st_mode
                relative = candidate.relative_to(source)
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise InstallError(f"Source contains a symlink or special file: {relative}")
                manifest.append((relative, stat.S_ISDIR(mode)))
    return manifest


def _write_new(path: Path, data: bytes, mode: int = 0o600):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)


def _public_roots(roots: list[Path]) -> list[Path]:
    if not isinstance(roots, (list, tuple)) or not roots:
        raise InstallError("Declare every existing HTTP document root with --public-root")
    canonical = []
    for root in roots:
        try:
            resolved = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            raise InstallError("Every declared HTTP document root must be an existing directory") from None
        if not resolved.is_dir():
            raise InstallError("Every declared HTTP document root must be an existing directory")
        if resolved not in canonical:
            canonical.append(resolved)
    return canonical


def _check_public_tree(destination: Path, roots: list[Path]):
    """Read-only bounded inspection of all currently reachable public paths.

    Python's simple HTTP server follows directory and file symlinks. An alias
    to a destination ancestor exposes a future installation even when the
    document root itself is a sibling. Dangling links to future project files
    are checked too. This is a preflight snapshot, not a same-UID sandbox.
    """
    unverified = "Declared HTTP document tree could not be fully verified within safety limits; installation refused"
    exposed = "An existing public-tree alias could expose the destination; choose a private location outside all reachable HTTP paths"
    deadline = time.monotonic() + MAX_PUBLIC_TREE_SECONDS
    seen = set()
    entry_count = 0

    def overlaps_project(path):
        return path == destination or path in destination.parents or destination in path.parents

    try:
        ancestor_inodes = {
            (info.st_dev, info.st_ino)
            for info in (ancestor.stat() for ancestor in destination.parents)
        }
        stack = [(root, 0) for root in roots]
        while stack:
            directory, depth = stack.pop()
            if time.monotonic() > deadline:
                raise InstallError(unverified)
            info = directory.stat()
            identity = (info.st_dev, info.st_ino)
            if identity in ancestor_inodes:
                raise InstallError(exposed)
            if identity in seen:
                continue
            if depth > MAX_PUBLIC_TREE_DEPTH or not stat.S_ISDIR(info.st_mode):
                raise InstallError(unverified)
            seen.add(identity)
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_PUBLIC_TREE_ENTRIES or time.monotonic() > deadline:
                        raise InstallError(unverified)
                    path = Path(entry.path)
                    entry_info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(entry_info.st_mode):
                        target = path.resolve(strict=False)
                        if overlaps_project(target):
                            raise InstallError(exposed)
                        try:
                            target_info = target.stat()
                        except FileNotFoundError:
                            # A dangling unrelated target cannot expose this
                            # installation; future changes remain out of scope.
                            continue
                        if stat.S_ISDIR(target_info.st_mode):
                            stack.append((target, depth + 1))
                    elif stat.S_ISDIR(entry_info.st_mode):
                        stack.append((path, depth + 1))
    except (OSError, RuntimeError):
        # Includes permissions, disappearing entries and unresolvable loops.
        # Do not assume inaccessible paths are harmless to another same-UID
        # server, and do not mutate any existing web content to make it pass.
        raise InstallError(unverified) from None


def install_project(destination: Path, *, source: Path | None = None, public_roots: list[Path]) -> dict:
    _preflight_runtime()
    exposed_roots = _public_roots(public_roots)
    source = Path(source) if source is not None else Path(__file__).absolute().parent.parent
    manifest = _source_manifest(source)
    requested = Path(destination).expanduser().absolute()
    if requested.name in {"", ".", ".."}:
        raise InstallError("Choose a new named project directory")
    # Resolve the user-selected existing parent, but never follow the final path.
    # This supports Termux's platform directory aliases without accepting a
    # pre-existing destination, including a dangling symlink.
    try:
        parent = requested.parent.resolve(strict=True)
    except OSError:
        raise InstallError("Destination parent must already exist") from None
    if not parent.is_dir():
        raise InstallError("Destination parent must be a directory")
    destination = parent / requested.name
    if any(destination == public_root or public_root in destination.parents for public_root in exposed_roots):
        raise InstallError("Destination is inside a declared HTTP document root; choose a private location outside every public root")
    if os.path.lexists(destination):
        raise InstallError("Destination already exists; choose a new project directory")
    _check_public_tree(destination, exposed_roots)
    # mkdir is atomic: a competing creator cannot turn this into an overwrite.
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise InstallError("Destination already exists; no files were changed") from None
    os.chmod(destination, 0o700)
    try:
        for relative, is_directory in manifest:
            target = destination / relative
            if is_directory:
                target.mkdir(mode=0o700)
                os.chmod(target, 0o700)
                continue
            descriptor = os.open(source / relative, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise InstallError("Source changed during installation")
                _write_new(target, stream.read())
        project_id = secrets.token_hex(16)
        _write_new(destination / "auth.key", secrets.token_hex(32).encode("ascii") + b"\n")
        project = {"project_id": project_id, "public_roots": [str(root) for root in exposed_roots]}
        _write_new(destination / "project.json", (json.dumps(project, indent=2) + "\n").encode("ascii"))
        runtime = destination / "runtime"
        runtime.mkdir(mode=0o700)
        os.chmod(runtime, 0o700)
        for name in ("inputs", "jobs", "results", "nonces"):
            (runtime / name).mkdir(mode=0o700)
            os.chmod(runtime / name, 0o700)
        start_script = (
            "#!/data/data/com.termux/files/usr/bin/sh\n"
            "# Analysis only. Defaults to loopback; no service starts during installation.\n"
            "set -eu\n"
            "umask 077\n"
            'APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
            'exec python3 -I "$APP_DIR/launch.py" serve '
            '--root "$APP_DIR/runtime" --key-file "$APP_DIR/auth.key" '
            f'--project-id {project_id} "$@"\n'
        )
        _write_new(destination / "start.sh", start_script.encode("utf-8"), 0o700)
    except Exception:
        # Only the fresh directory may be partial. Do not recursively delete or
        # attempt any repair that might affect user files or another process.
        raise InstallError("Installation incomplete in the new directory; existing files were not changed") from None
    return {"destination": str(destination), "project_id": project_id, "started": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a new private Termux analysis project (never starts it)")
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--public-root", type=Path, action="append", help="Actual existing HTTP document root; repeat for every server. Destination must be outside all roots")
    parser.add_argument("--discover-http-port", type=int, help="Read-only local discovery of the current user's Python HTTP server document root on this port")
    args = parser.parse_args(argv)
    if not args.public_root and args.discover_http_port is None:
        parser.error("at least one of --public-root or --discover-http-port is required")
    if args.discover_http_port is not None and not 1 <= args.discover_http_port <= 65535:
        parser.error("--discover-http-port must be between 1 and 65535")
    public_roots = list(args.public_root or [])
    if args.discover_http_port is not None:
        from .discovery import DiscoveryError, discover_http_roots
        try:
            public_roots.extend(discover_http_roots(args.discover_http_port))
        except DiscoveryError as error:
            print(f"Manager installer: HTTP document root discovery failed ({error})", file=sys.stderr)
            return 1
    try:
        result = install_project(args.dest, public_roots=public_roots)
    except (InstallError, OSError) as error:
        # Path errors contain no secret key; installation errors are deliberately
        # concise and never include copied content or generated credentials.
        print(f"Manager installer: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print("Start manually: sh " + shlex.quote(str(Path(result["destination"]) / "start.sh")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
