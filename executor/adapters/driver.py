"""The CLI skeleton shared by adapters that drive an external runner.

Every adapter here answers the same two commands:

    python -m executor.adapters.<name>_stream --root DIR --collect-out FILE
    python -m executor.adapters.<name>_stream --root DIR --ledger FILE
                                           [--tests-file FILE]

`discover` must not execute tests; `run` must append ledger rows as units
finish (so a SIGKILL keeps them) and write `suite_end` only after the runner
really reached the end of the suite. A missing tool or an unusable project is
reported as a RuntimeError with an actionable message: hh prints it and fails
the attempt instead of recording an empty, "complete" suite.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(name: str, discover, run, argv=None) -> int:
    ap = argparse.ArgumentParser(prog=f"executor.adapters.{name}_stream")
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--tests-file")
    ap.add_argument("--collect-out")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    try:
        if args.collect_out:
            return discover(root, args.collect_out)
        if not args.ledger:
            ap.error("--ledger is required unless --collect-out is given")
        return run(root, args.ledger, args.tests_file)
    except RuntimeError as e:
        print(f"hh: {e}", file=sys.stderr)
        return 2


def which_error(tool: str, hint: str) -> RuntimeError:
    return RuntimeError(f"hh's {tool} adapter needs `{tool}`. {hint}")
