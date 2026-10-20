"""Run an RSpec suite (Ruby) through hh.

  * discovery: `rspec --dry-run --format json` — RSpec loads the spec files and
    reports every example (id, description, file) without running any of them.
  * execution: `rspec --require rspec_formatter.rb --format HhRspecFormatter`
    with the selected example ids (`./spec/a_spec.rb[1:2]`). RSpec calls the
    formatter as each example finishes, so the ledger streams and a kill keeps
    finished rows — RSpec's own JSON report is only written at the end.

Granularity is the example, and a resume re-runs only examples that never got a
row: RSpec accepts example ids on the command line, so that is exact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from . import base, driver

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATTER = os.path.join(HERE, "rspec_formatter.rb")


def rspec_bin() -> str:
    explicit = os.environ.get("HH_RSPEC")
    if explicit:
        return explicit
    found = shutil.which("rspec")
    if not found:
        raise driver.which_error("rspec", "Install it with `gem install rspec` "
                                          "or set HH_RSPEC.")
    return found


def key_for(example_id: str) -> str:
    """`./spec/a_spec.rb[1:2]` -> `rspec::spec/a_spec.rb/1:2`.

    The file stays a path segment so `keys.file_of` aggregates per spec file,
    exactly like the other per-test runners aggregate per module.
    """
    path, _, suffix = example_id.partition("[")
    if path.startswith("./"):
        path = path[2:]
    return f"rspec::{path}/{suffix.rstrip(']')}"


def _argv(root: str, *args: str) -> list[str]:
    local = os.path.join(root, "bin", "rspec")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return [local, *args]
    bundle = shutil.which("bundle")
    if bundle and os.path.exists(os.path.join(root, "Gemfile")):
        return [bundle, "exec", "rspec", *args]
    return [rspec_bin(), *args]


def _json_report(tool: str, root: str) -> dict:
    """Run rspec with --dry-run and return its JSON report."""
    import tempfile
    fd, path = tempfile.mkstemp(prefix="hh-rspec-", suffix=".json")
    os.close(fd)
    try:
        proc = subprocess.run(
            [*tool, "--dry-run", "--format", "json", "--out", path,
             "--no-color"],
            cwd=root, capture_output=True, text=True)
        if proc.returncode not in (0, 1) or not os.path.getsize(path):
            raise RuntimeError("rspec could not list the examples:\n"
                               + (proc.stdout + proc.stderr)[-2000:])
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def discover(root: str, out_path: str) -> int:
    doc = _json_report(_argv(root), root)
    rows = []
    for example in doc.get("examples", []) or []:
        example_id = example.get("id")
        if example_id:
            rows.append((example_id, key_for(example_id)))
    base.write_ids(out_path, rows)
    return 0


def run(root: str, ledger: str, tests_file: str | None) -> int:
    done = base.done_ids(ledger)
    wanted = base.selection_ids(tests_file)
    if wanted is None:
        doc = _json_report(_argv(root), root)
        wanted = [e["id"] for e in doc.get("examples", []) or [] if e.get("id")]
    targets = [raw for raw in wanted if raw not in done]

    written = 0
    if targets:
        env = dict(os.environ)
        env["HH_RSPEC_LEDGER"] = ledger
        cmd = _argv(root, "--require", FORMATTER,
                    "--format", "HhRspecFormatter", "--no-color", *targets)
        proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail: list[str] = []
        for line in proc.stdout:                      # type: ignore[union-attr]
            tail.append(line)
            del tail[:-40]
        rc = proc.wait()
        written = len(_rows_for(ledger, targets))
        noise = "".join(tail)
        # RSpec exits 1 both for "tests failed" (rows written) and for "this
        # file does not load" (no rows at all). The second one is an adapter
        # error, not a suite where every id stopped existing.
        load_error = any(marker in noise for marker in (
            "error occurred while loading", "cannot load such file",
            "LoadError", "SyntaxError"))
        if written == 0 and (rc not in (0, 1) or load_error):
            raise RuntimeError(f"rspec failed with exit code {rc}:\n"
                               + noise[-1500:])
    base.suite_end_row(ledger, written)
    return 0


def _rows_for(ledger: str, targets: list[str]) -> list[str]:
    """Which of `targets` got a row in this attempt (for the suite_end count)."""
    done = base.done_ids(ledger)
    return [t for t in targets if t in done]


def main(argv=None) -> int:
    return driver.main("rspec", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
