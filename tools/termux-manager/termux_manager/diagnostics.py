"""Fixed-vocabulary local diagnostics. Never render exceptions or user values."""
from __future__ import annotations

import argparse
import errno
import json
import sys

STAGES = frozenset({"arguments", "bind_config", "project_config", "identity",
                    "authentication_key", "runtime", "server_lock", "socket_bind",
                    "serve", "shutdown"})
CODES = frozenset({"invalid_arguments", "invalid_bind", "invalid_port",
                   "invalid_project_id", "unsafe_directory", "unsafe_file",
                   "invalid_key", "invalid_json", "object_too_large", "not_found",
                   "lock_open_failed", "unsafe_server_lock", "manager_already_running",
                   "lock_unavailable", "socket_unavailable", "io_error",
                   "validation_failed", "unexpected_failure"})


class StartupFailure(ValueError):
    def __init__(self, stage, code):
        self.stage, self.code = stage, code
        super().__init__(code)


def failure_record(stage, error):
    """Bounded causal inspection; exception text/paths/args are never emitted."""
    if isinstance(error, StartupFailure):
        stage = error.stage
    stage = stage if stage in STAGES else "arguments"
    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in CODES:
        code = ("io_error" if isinstance(error, OSError) else
                "validation_failed" if isinstance(error, ValueError) else "unexpected_failure")
    number = None
    seen = set()
    current = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        candidate = getattr(current, "errno", None)
        if isinstance(current, OSError) and type(candidate) is int and candidate in errno.errorcode:
            number = candidate
            break
        current = current.__cause__ or current.__context__
    return {"event": "manager_start_failed", "stage": stage, "code": code,
            "errno": errno.errorcode.get(number), "execution_enabled": False,
            "python": ".".join(str(part) for part in sys.version_info[:3])}


def report_failure(stage, error):
    print(json.dumps(failure_record(stage, error), sort_keys=True), file=sys.stderr)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default errors can echo arbitrary supplied option values.
        report_failure("arguments", StartupFailure("arguments", "invalid_arguments"))
        raise SystemExit(2)
