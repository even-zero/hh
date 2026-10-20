"""Run a Go test suite through hh.

`go test` already has the two properties hh needs, so this adapter mostly
translates its machine output:

  * discovery: `go test -list '.*' ./...` lists test, example and fuzz
    functions without running them (the package's test binary is built, which
    is the cost of knowing what will run).
  * execution: `go test -json -count=1` streams one JSON event per test as it
    starts and finishes, so a kill at the budget keeps every finished row.
    `-count=1` defeats the result cache: a cached run reports `(cached)` and
    ~0 s, which would teach the store that the suite is instant.

Granularity is the test. On a resume, selected tests are grouped by package
(`go test -run` is a per-invocation regex) so a package only pays for the tests
that are still missing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from . import base, driver

PACKAGE_LINE = re.compile(r"^(ok|FAIL|\?)\s+(\S+)")


def go_bin() -> str:
    explicit = os.environ.get("HH_GO")
    if explicit:
        return explicit
    found = shutil.which("go")
    if not found:
        raise driver.which_error("go", "Install the Go toolchain or set HH_GO.")
    return found


def key_for(pkg: str, name: str) -> str:
    return f"go::{pkg}/{name}"


def raw_for(pkg: str, name: str) -> str:
    return f"{pkg}::{name}"


def split_raw(raw: str) -> tuple[str, str]:
    pkg, _, name = raw.rpartition("::")
    return pkg, name


def parse_list_output(text: str) -> list[tuple[str, str]]:
    """[(package, test name)] from `go test -list` output.

    `go test -list` prints a package's test names and then a package line
    (`ok  pkg  0.01s`, `?   pkg  [no test files]`, `FAIL  pkg  [build failed]`),
    so names are attributed to the package line that follows them.
    """
    found: list[tuple[str, str]] = []
    pending: list[str] = []
    failed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = PACKAGE_LINE.match(stripped)
        if m:
            verdict, pkg = m.group(1), m.group(2)
            if verdict == "ok" and pending:
                for name in pending:
                    if not name.startswith("Benchmark"):
                        found.append((pkg, name))
            if verdict == "FAIL" and "[build failed]" in stripped:
                failed.append(pkg)
            pending = []
        else:
            pending.append(stripped)
    if failed:
        raise RuntimeError("go could not build " + ", ".join(failed)
                           + ", so its tests cannot be enumerated")
    if pending:
        raise RuntimeError(
            "go printed test names but no package line, so their package is "
            "unknown; names: " + ", ".join(pending[:5]))
    # De-duplicate while keeping the order.
    seen: set[tuple[str, str]] = set()
    unique = []
    for pkg, name in found:
        if (pkg, name) not in seen:
            seen.add((pkg, name))
            unique.append((pkg, name))
    return unique


def discover_tests(go: str, root: str) -> list[tuple[str, str]]:
    """[(package, test name)] from `go test -list`; nothing is executed."""
    proc = subprocess.run([go, "test", "-list", ".*", "./..."], cwd=root,
                          capture_output=True, text=True)
    try:
        return parse_list_output(proc.stdout)
    except RuntimeError as e:
        if proc.returncode == 0 or PACKAGE_LINE.search(proc.stdout):
            raise
        raise RuntimeError("go could not list the tests:\n"
                           + (proc.stdout + proc.stderr)[-2000:]) from e


def discover(root: str, out_path: str) -> int:
    tests = discover_tests(go_bin(), root)
    base.write_ids(out_path, [(raw_for(p, n), key_for(p, n)) for p, n in tests])
    return 0


def _status(action: str) -> str | None:
    return {"pass": "passed", "fail": "failed", "skip": "skipped"}.get(action)


def consume_events(lines, ledger: str, written: int, tail: list[str],
                   allowed: set[str] | None = None) -> tuple[int, dict]:
    """Read `go test -json` events, write rows as tests finish.

    `allowed` restricts rows to the selection: a tool that runs more than it
    was asked to (a looser filter, a package that ran in full) must not add
    measurements hh did not select.
    """
    stats = {"rows": written, "build_fail": []}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            if line:
                tail.append(line)
                del tail[:-40]
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        action = ev.get("Action")
        test = ev.get("Test")
        pkg = ev.get("Package", "")
        if action == "output" and not test:
            tail.append(ev.get("Output", ""))
            del tail[:-40]
            continue
        if action in ("build-fail", "build-output"):
            if action == "build-fail":
                stats["build_fail"].append(pkg)
            tail.append(f"{pkg}: build failed")
            del tail[:-40]
            continue
        if not test or "/" in test:
            continue                                  # package event, or a subtest
        raw = raw_for(pkg, test)
        if allowed is not None and raw not in allowed:
            continue
        if action == "run":
            base.start_row(ledger, key_for(pkg, test), raw)
        elif action in ("pass", "fail", "skip"):
            ms = float(ev.get("Elapsed") or 0.0) * 1000.0
            base.end_row(ledger, key_for(pkg, test), raw,
                         _status(action), ms)
            stats["rows"] += 1
    return stats["rows"], stats


def _run_json(go: str, root: str, args: list[str], ledger: str, written: int,
              allowed: set[str] | None = None) -> int:
    cmd = [go, "test", "-json", "-count=1", *args]
    proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail: list[str] = []
    written, stats = consume_events(proc.stdout, ledger, written, tail, allowed)
    rc = proc.wait()
    if stats["build_fail"]:
        raise RuntimeError("go could not build: "
                           + ", ".join(sorted(set(stats["build_fail"])))
                           + "\n" + "".join(tail)[-1500:])
    if rc not in (0, 1) and written == 0:
        raise RuntimeError(f"go test failed with exit code {rc}:\n"
                           + "".join(tail)[-1500:])
    return written


def run(root: str, ledger: str, tests_file: str | None) -> int:
    go = go_bin()
    done = base.done_ids(ledger)
    wanted = base.selection_ids(tests_file)
    written = 0
    if wanted is None and not done:
        # Cold record pass: exactly the naive command, one process.
        written = _run_json(go, root, ["./..."], ledger, written)
    else:
        if wanted is None:
            pairs = discover_tests(go, root)
        else:
            pairs = [split_raw(raw) for raw in wanted]
        pairs = [(p, n) for p, n in pairs if raw_for(p, n) not in done]
        by_pkg: dict[str, list[str]] = {}
        for pkg, name in pairs:
            by_pkg.setdefault(pkg, []).append(name)
        allowed = {raw_for(p, n) for p, n in pairs}
        for pkg in sorted(by_pkg):
            names = sorted(by_pkg[pkg])
            written = _run_json(go, root,
                                ["-run", "^(" + "|".join(re.escape(n) for n in names) + ")$",
                                 pkg],
                                ledger, written, allowed)
    base.suite_end_row(ledger, written)
    return 0


def main(argv=None) -> int:
    return driver.main("go", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
