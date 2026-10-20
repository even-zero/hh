"""Run a CTest suite (CMake / C / C++) through hh.

Works for anything registered with `add_test` — plain C tests, GoogleTest via
`gtest_discover_tests`, Catch2 via `catch_discover_tests`. CTest is the runner;
the framework underneath does not matter.

  * discovery: `ctest --show-only=json-v1` — the test list, without running
    anything (CMake >= 3.14). Tests marked `DISABLED` are left out: CTest never
    runs them, so recording them would only produce ids that never resolve.
  * execution: `ctest -R '^(name1|name2)$' --output-on-failure`, in the build
    directory, parsing the numbered summary line CTest prints as each test
    finishes (`1/4 Test #1: name ....   Passed    0.01 sec`). That line
    streams, so a SIGKILL keeps every finished test.

`--root` is the **build** directory (the one holding `CTestTestfile.cmake`).
When it is the source directory, hh looks for a single configured build tree
below it (`build/`, `cmake-build-debug/`, …) and uses that.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from . import base, driver

# `1/4 Test #1: ok_a .............................   Passed    0.00 sec`
SUMMARY = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>.+?)\s+\.{3,}\s*"
    r"(?P<status>\S.*?)\s+(?P<sec>\d+(?:\.\d+)?)\s+sec\s*$")
START = re.compile(r"^\s*Start\s+\d+:\s+(?P<name>.+?)\s*$")

SKIP_DIRS = {".git", "node_modules", ".hh", "__pycache__", ".venv", "venv",
             ".cache", "vcpkg_installed", "dist", "out"}


def ctest_bin() -> str:
    explicit = os.environ.get("HH_CTEST")
    if explicit:
        return explicit
    found = shutil.which("ctest")
    if not found:
        raise driver.which_error("ctest", "Install CMake (>= 3.14) or set HH_CTEST.")
    return found


def _has_ctest_file(path: str) -> bool:
    return os.path.exists(os.path.join(path, "CTestTestfile.cmake"))


def build_dir(root: str) -> str:
    """The CTest build tree: `root` itself, or the single configured tree below it."""
    if _has_ctest_file(root):
        return root
    candidates: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= 4:
            dirnames[:] = []
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        if _has_ctest_file(dirpath):
            candidates.append(dirpath)
            dirnames[:] = []          # a build tree has no nested build trees
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(
            f"no CTestTestfile.cmake under {root}: configure and build the "
            "project first (cmake -S . -B build && cmake --build build), or "
            "pass --root <build dir>.")
    raise RuntimeError(
        "several CTest build trees found under " + root + ": "
        + ", ".join(sorted(os.path.relpath(c, root) for c in candidates))
        + ". Pass --root with the one to time.")


def _ctest_json(ctest: str, build: str) -> dict:
    proc = subprocess.run([ctest, "--show-only=json-v1"], cwd=build,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ctest could not list the tests:\n"
                           + (proc.stdout + proc.stderr)[-1500:])
    try:
        return json.loads(proc.stdout)
    except ValueError as e:
        raise RuntimeError(f"ctest --show-only=json-v1 is not valid JSON ({e}); "
                           "CMake >= 3.14 is required.")


def test_names(ctest: str, build: str) -> list[str]:
    names = []
    for test in _ctest_json(ctest, build).get("tests", []) or []:
        name = test.get("name")
        if not name:
            continue
        props = {p.get("name") for p in test.get("properties", []) or []
                 if isinstance(p, dict)}
        if "DISABLED" in props:
            continue
        names.append(name)
    return sorted(set(names))


def key_for(name: str) -> str:
    return f"ctest::{name}"


def discover(root: str, out_path: str) -> int:
    names = test_names(ctest_bin(), build_dir(root))
    base.write_ids(out_path, [(n, key_for(n)) for n in names])
    return 0


def _row_status(text: str) -> str:
    low = text.strip().lower()
    if low.startswith("passed"):
        return "passed"
    if "skipped" in low:
        return "skipped"
    if "not run" in low and "disabled" in low:
        return "skipped"
    return "failed"              # Failed, Timeout, Exception, Not Run


def run(root: str, ledger: str, tests_file: str | None) -> int:
    ctest = ctest_bin()
    build = build_dir(root)
    wanted = base.selection_ids(tests_file)
    if wanted is None:
        wanted = test_names(ctest, build)
    done = base.done_ids(ledger)
    targets = [t for t in wanted if t not in done]
    target_set = set(targets)
    written = 0
    if target_set:
        # CTest has its own regex engine (KWSys): no `(?:...)`, no backslash
        # classes it does not know. A plain group and re.escape is what it can
        # take. An invalid regex is *not* an error exit code — CTest prints
        # "RegularExpression::compile()" and exits 0 with no tests — so the
        # marker is checked below; otherwise a bad filter would look like a
        # suite with nothing left to do.
        regex = "^(" + "|".join(re.escape(t) for t in targets) + ")$"
        cmd = [ctest, "-R", regex, "--output-on-failure", "--no-tests=ignore"]
        proc = subprocess.Popen(cmd, cwd=build, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail: list[str] = []
        for line in proc.stdout:                      # type: ignore[union-attr]
            tail.append(line)
            del tail[:-40]
            m = SUMMARY.match(line)
            if not m:
                continue
            name = m.group("name").strip()
            if name not in target_set:
                continue
            status = _row_status(m.group("status"))
            ms = float(m.group("sec")) * 1000.0
            base.end_row(ledger, key_for(name), name, status, ms)
            written += 1
        rc = proc.wait()
        noise = "".join(tail)
        if "RegularExpression::compile()" in noise:
            raise RuntimeError("ctest rejected the test filter:\n" + noise[-800:])
        if rc not in (0, 8) and not written:
            raise RuntimeError(f"ctest failed with exit code {rc}:\n" + noise[-800:])
    base.suite_end_row(ledger, written)
    return 0


def main(argv=None) -> int:
    return driver.main("ctest", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
