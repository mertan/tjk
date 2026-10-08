"""Isolated, read-only CLI for the public-data watchlist adapter."""
import json
import math
import os
from pathlib import Path
import stat
import sys

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 20
MAX_NODES = 1000000


class InputError(ValueError):
    """Only fixed error codes may leave this module."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InputError("invalid_json")
        result[key] = value
    return result


def _constant(_value):
    raise InputError("invalid_json")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise InputError("invalid_json")
    return number


def _bounded_tree(value):
    # Iterators keep traversal memory proportional to depth, not array width.
    pending = [(iter((value,)), 0)]
    count = 0
    while pending:
        children, depth = pending[-1]
        try:
            item = next(children)
        except StopIteration:
            pending.pop()
            continue
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise InputError("input_too_complex")
        if isinstance(item, dict):
            pending.append((iter(item.values()), depth + 1))
        elif isinstance(item, list):
            pending.append((iter(item), depth + 1))


def _read_json(filename):
    """Open all path components without following symlinks; never write."""
    descriptor = None
    directory = None
    try:
        if not filename or "\x00" in filename:
            raise InputError("unsafe_input_file")
        path = Path(os.path.abspath(filename))
        # O_PATH permits traversal of Android parents that are not listable.
        if not hasattr(os, "O_PATH") or not hasattr(os, "O_NOFOLLOW"):
            raise InputError("unsupported_file_safety")
        directory_flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(path.anchor, directory_flags)
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise InputError("unsafe_input_file")
        if info.st_size > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        chunks = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_INPUT_BYTES:
            raise InputError("input_too_large")
        result = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=_constant, parse_float=_float,
        )
        if not isinstance(result, dict):
            raise InputError("invalid_input_shape")
        _bounded_tree(result)
        return result
    except InputError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise InputError("invalid_json") from None
    except OSError:
        raise InputError("unsafe_input_file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _arguments(argv):
    if argv == ["--help"]:
        return None
    values = {}
    index = 0
    while index < len(argv):
        option = argv[index]
        if option not in ("--observations", "--context", "--provider", "--symbols") or option in values:
            raise InputError("invalid_arguments")
        if index + 1 == len(argv) or argv[index + 1].startswith("--"):
            raise InputError("invalid_arguments")
        values[option] = argv[index + 1]
        index += 2
    if "--provider" in values:
        if values["--provider"] != "fintable" or any(k in values for k in ("--observations", "--context")):
            raise InputError("invalid_arguments")
    elif "--symbols" in values:
        raise InputError("invalid_arguments")
    if "--symbols" in values:
        from .public_adapter import SYMBOL
        symbols = values["--symbols"].split(",")
        if not 1 <= len(symbols) <= 20 or len(set(symbols)) != len(symbols) or any(not SYMBOL.fullmatch(s) for s in symbols):
            raise InputError("invalid_arguments")
        values["--symbols"] = symbols
    return values


def _scan(**kwargs):
    # Import only the read-only adapter; do not initialize manager/auth/storage.
    from .public_adapter import scan
    return scan(**kwargs)


def _failure(code):
    return {
        "decision": "PAS", "data_status": "DATA_UNAVAILABLE",
        "execution_enabled": False, "error": code,
    }


def main(argv=None):
    try:
        arguments = _arguments(list(sys.argv[1:] if argv is None else argv))
        if arguments is None:
            print("Usage: python3 -I -B public_scan.py "
                  "[--observations FILE] [--context FILE]\n"
                  "       python3 -I -B public_scan.py --provider fintable [--symbols AAA,BBB]\n"
                  "Personal noncommercial research; max 20 directory symbols; no order execution.")
            return 0
        observations = (_read_json(arguments["--observations"])
                        if "--observations" in arguments else None)
        context = (_read_json(arguments["--context"])
                   if "--context" in arguments else None)
        provider_options = ({"provider": arguments["--provider"], "symbols": arguments.get("--symbols")}
                            if "--provider" in arguments else {})
        result = _scan(
            observations=observations, context=context,
            environ={"SEC_USER_AGENT": os.environ.get("SEC_USER_AGENT", "")},
            **provider_options,
        )
        if not isinstance(result, dict):
            raise ValueError("invalid_report")
        output = json.dumps(result, ensure_ascii=False, allow_nan=False)
        status = 0
    except InputError as exc:
        output = json.dumps(_failure(str(exc)))
        status = 2
    except Exception:
        output = json.dumps(_failure("scan_failed"))
        status = 2
    print(output)
    return status
