"""Run a PHPUnit suite (PHP) through hh.

  * discovery: `phpunit --list-tests-xml <file>` (PHPUnit >= 9.4) writes the
    test methods as XML — `testMethod id="Tests\\Unit\\CalcTest::testAdd"` — so
    the list is machine-readable and nothing is executed.
  * execution: `phpunit --teamcity` streams TeamCity service messages as each
    test starts and finishes. The `testStarted` message carries a `locationHint`
    of `php_qn://<file>::\\<Class>::<method>`: that is the test id, and it means
    a ledger row can be written the moment a test finishes, so a SIGKILL keeps
    everything before it.

Granularity is the test. A resume passes `--filter` a pattern built from the
ids still missing.

Data providers: one method can run many data sets, and PHPUnit reports each of
them separately. hh records the method once, with the status of the worst data
set and the sum of their durations, and flushes it when the next method starts
(or at the end): the unit stays "the test method", the same unit discovery
reports.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from . import base, driver

TC_MESSAGE = re.compile(r"##teamcity\[(?P<name>[A-Za-z]+)(?P<attrs>[^\]]*)\]")
TC_ATTR = re.compile(r"([A-Za-z]+)='((?:[^'|]|\|.)*)'")
TC_ESCAPES = {"n": "\n", "r": "\r", "'": "'", "[": "[", "]": "]",
              "|": "|", "\\": "\\"}


def _unescape(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "|" and i + 1 < len(value):
            out.append(TC_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_teamcity(line: str) -> tuple[str, dict[str, str]] | None:
    m = TC_MESSAGE.search(line)
    if not m:
        return None
    attrs = {k: _unescape(v) for k, v in TC_ATTR.findall(m.group("attrs"))}
    return m.group("name"), attrs


def fqn_from_hint(hint: str) -> str | None:
    """`php_qn:///a/Tests/FooTest.php::\\Tests\\FooTest::testAdd with data set #0`
    -> `Tests\\FooTest::testAdd`."""
    if not hint.startswith("php_qn://"):
        return None
    parts = hint.split("::", 2)
    if len(parts) != 3:
        return None
    cls, method = parts[1].lstrip("\\"), parts[2]
    method = re.split(r"\s+with data set|\s*#\d+$", method)[0]
    return f"{cls}::{method}"


def key_for(test_id: str) -> str:
    """`Tests\\Unit\\CalcTest::testAdd` -> `phpunit::Tests/Unit/CalcTest/testAdd`."""
    path = test_id.lstrip("\\").replace("\\", "/")
    return "phpunit::" + path.replace("::", "/")


def _php() -> str | None:
    return os.environ.get("HH_PHP") or shutil.which("php")


def phpunit_argv(root: str) -> list[str]:
    explicit = os.environ.get("HH_PHPUNIT")
    if explicit:
        return [explicit]
    for candidate in (os.path.join(root, "vendor", "bin", "phpunit"),
                      os.path.join(root, "phpunit.phar")):
        if os.path.isfile(candidate):
            if os.access(candidate, os.X_OK):
                return [candidate]
            php = _php()
            if php:
                return [php, candidate]
    found = shutil.which("phpunit")
    if found:
        return [found]
    raise driver.which_error(
        "phpunit", "Install it with Composer (vendor/bin/phpunit), download "
                   "phpunit.phar into the project, or set HH_PHPUNIT.")


def discover_ids(root: str) -> list[str]:
    fd, path = tempfile.mkstemp(prefix="hh-phpunit-", suffix=".xml")
    os.close(fd)
    try:
        proc = subprocess.run([*phpunit_argv(root), "--list-tests-xml", path],
                              cwd=root, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.getsize(path):
            raise RuntimeError(
                "phpunit could not list the tests (PHPUnit >= 9.4 is needed "
                "for --list-tests-xml):\n"
                + (proc.stdout + proc.stderr)[-2000:])
        with open(path, "rb") as f:
            tree = ET.parse(f)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    ids: list[str] = []
    for elem in tree.getroot().iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "testMethod" and elem.get("id"):
            ids.append(elem.get("id"))
        elif tag == "phpt" and elem.get("file"):
            ids.append(elem.get("file"))
    return list(dict.fromkeys(ids))


def discover(root: str, out_path: str) -> int:
    ids = discover_ids(root)
    base.write_ids(out_path, [(i, key_for(i)) for i in ids])
    return 0


def _filter_pattern(targets: list[str]) -> str:
    # PHPUnit matches the pattern against `Class::method` (data sets append a
    # suffix). Anchored at the start, and not allowed to run on into another
    # identifier, so `Foo::testA` does not select `Foo::testAB`.
    body = "|".join(re.escape(t.lstrip("\\")) for t in targets)
    return f"/^(?:{body})(?![A-Za-z0-9_])/"


def run(root: str, ledger: str, tests_file: str | None) -> int:
    done = base.done_ids(ledger)
    wanted = base.selection_ids(tests_file)
    if wanted is None:
        wanted = discover_ids(root)
    targets = [raw for raw in wanted if raw not in done]
    by_name: dict[str, list[str]] = {}
    for raw in wanted:
        by_name.setdefault(raw.rsplit("::", 1)[-1], []).append(raw)

    written = 0
    current: str | None = None
    pending: dict | None = None

    def flush() -> None:
        nonlocal pending, written
        if pending is None:
            return
        base.end_row(ledger, key_for(pending["id"]), pending["id"],
                     pending["status"], pending["ms"])
        written += 1
        pending = None

    if targets:
        cmd = [*phpunit_argv(root), "--teamcity", "--do-not-cache-result"]
        if len(targets) < len(set(wanted)):
            cmd += ["--filter", _filter_pattern(targets)]
        proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail: list[str] = []
        for line in proc.stdout:                      # type: ignore[union-attr]
            parsed = parse_teamcity(line)
            if not parsed:
                if line.strip():
                    tail.append(line)
                    del tail[:-40]
                continue
            kind, attrs = parsed
            name = attrs.get("name", "")
            if kind == "testStarted":
                test_id = fqn_from_hint(attrs.get("locationHint", ""))
                if not test_id:
                    base_name = re.split(r"\s+with data set|#\d+$", name)[0]
                    candidates = by_name.get(base_name, [])
                    test_id = candidates[0] if len(candidates) == 1 else None
                if not test_id:
                    current = None
                    continue
                if test_id != current:
                    flush()                            # the previous method is done
                    current = test_id
                pending = pending or {"id": test_id, "status": "passed", "ms": 0.0}
            elif kind == "testFailed" and pending is not None:
                pending["status"] = "failed"
            elif kind == "testIgnored" and pending is not None:
                if pending["status"] != "failed":
                    pending["status"] = "skipped"
            elif kind == "testFinished" and pending is not None:
                try:
                    pending["ms"] += float(attrs.get("duration") or 0)
                except ValueError:
                    pass
        rc = proc.wait()
        flush()
        noise = "".join(tail)
        if written == 0 and rc not in (0, 1):
            raise RuntimeError(f"phpunit failed with exit code {rc}:\n"
                               + noise[-1500:])
    base.suite_end_row(ledger, written)
    return 0


def main(argv=None) -> int:
    return driver.main("phpunit", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
