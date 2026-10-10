#!/usr/bin/env python3
"""Use python3 -I launch.py to ignore PYTHONPATH, user site and current directory."""
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))
sys.dont_write_bytecode = True


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: python3 -I launch.py install|serve|client ...", file=sys.stderr)
        return 2
    command = args.pop(0)
    if command == "install":
        from termux_manager.install import main as run
    elif command == "serve":
        from termux_manager.server import main as run
    elif command == "client":
        from termux_manager.client import main as run
    else:
        print("Unsupported command", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
