#!/usr/bin/env python3
"""One read-only scan: python3 -I -B public_scan.py [--observations FILE]."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from equity_guard.public_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
