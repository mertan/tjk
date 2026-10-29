#!/usr/bin/env python3
"""Download permitted history or persist an honest forward-test readiness check."""
import argparse
import json
from pathlib import Path

from equity_guard.forward import ForwardJournal, make_check
from equity_guard.public_cli import _read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("history")
    download.add_argument("--symbols", required=True, help="Explicit comma-separated symbols; max 20")
    download.add_argument("--output-dir", required=True)
    check = commands.add_parser("check")
    check.add_argument("--journal", required=True)
    check.add_argument("--history-manifest")
    check.add_argument("--output")
    arguments = parser.parse_args(argv)
    reserved_output = None
    appended = False
    try:
        if arguments.command == "history":
            from equity_guard.history import download_history
            result = download_history(arguments.symbols.split(","), arguments.output_dir)
        else:
            history = _read_json(arguments.history_manifest) if arguments.history_manifest else None
            if arguments.output:
                output = Path(arguments.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                # Reserve before appending: an existing output file must not
                # cause an invisible extra database event on each retry.
                reserved_output = output.open("x", encoding="utf-8")
            event = make_check(history_manifest=history)
            # A connection cannot be marked available from a CLI flag or a
            # past snapshot. A collector and trained model remain required.
            result = ForwardJournal(arguments.journal).append_check(event)
            appended = True
        rendered = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        if reserved_output:
            try:
                reserved_output.write(rendered + "\n")
                reserved_output.close()
            except OSError:
                # Persistence to the journal already succeeded; report that
                # explicitly instead of claiming the whole action failed.
                result["report_write_error"] = "output_write_failed_after_journal_append"
                print(json.dumps(result, ensure_ascii=False))
                return 2
        print(rendered)
        return 0 if arguments.command == "check" or result.get("retrieval_complete") is True else 2
    except Exception:
        # Provider responses, local paths and credentials are not diagnostics.
        print(json.dumps({"status": "BLOCKED", "reason": "forward_research_failed",
                          "predictions": [], "execution_enabled": False}))
        return 2
    finally:
        if reserved_output:
            reserved_output.close()
            if not appended:
                Path(arguments.output).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
