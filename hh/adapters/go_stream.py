"""Tier A live wrapper: stream `go test -json` into a hh ledger as tests finish.

    python3 -m hh.adapters.go_stream --ledger .hh/ledger.jsonl -- \
        go test -json -count=1 ./...

The wrapper never buffers: one flush per terminal event (Law 2), so a SIGKILL
(or a CI timeout) leaves every finished Go test checkpointed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from .base import end_row
from .go_test import terminal_row


def stream(cmd: list[str], ledger: str, cwd: str | None = None, env=None) -> int:
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, text=True,
                         bufsize=1)
    assert p.stdout is not None
    for line in p.stdout:                       # line-buffered → rows land live
        row = terminal_row(line)
        if row is not None:
            end_row(ledger, row["key"], row["raw_id"], row["status"],
                    row["duration_ms"], row["censored"])
    return p.wait()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="go_stream")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--cwd")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd   # strip REMAINDER '--'
    if not cmd:
        print("go_stream: no command given", file=sys.stderr)
        return 2
    return stream(cmd, a.ledger, a.cwd)


if __name__ == "__main__":
    sys.exit(main())
