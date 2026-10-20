"""Run a Rust test suite through cargo-nextest.

nextest is a runner, not just a reporter: it has a machine-readable listing and
a machine-readable event stream, which is exactly the pair hh needs.

  * discovery: `cargo nextest list --message-format json` — the binaries and
    their test cases, without running them. Ignored tests are left out: nextest
    does not run them either, so recording them would only create ids that
    never resolve.
  * execution: `cargo nextest run --message-format json`, one JSON event per
    line (`test-started`, `test-passed`, `test-failed`, `test-skipped`), so a
    kill at the budget keeps every finished row.

Granularity is the test. A resume passes nextest a filter expression
(`-E 'binary_id(="…") & test(="…") | …'`) so only the tests still missing run.

nextest's list schema has changed between releases (dict-of-suites in 0.9.x,
list-of-suites in the 0.9.9x line), and its event schema changed once too
(`{"type":"test","event":"ok"}` before 0.9.38, `{"type":"test-passed"}` after).
Both forms are accepted; your own output is the version that matters, and the
integration test in this repository runs whichever is installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from . import base, driver

TEST_FAILED_EXIT = 100


def nextest_bin() -> str:
    explicit = os.environ.get("HH_NEXTEST")
    if explicit:
        return explicit
    found = shutil.which("cargo-nextest") or shutil.which("cargo")
    if not found:
        raise driver.which_error(
            "cargo nextest",
            "Install Rust and cargo-nextest (cargo install cargo-nextest, or "
            "`cargo binstall cargo-nextest`), or set HH_NEXTEST.")
    return found


def _cargo_argv(tool: str, *args: str) -> list[str]:
    """`cargo nextest …`, or a direct `cargo-nextest` binary if that is what HH_NEXTEST is."""
    if os.path.basename(tool).startswith("cargo-nextest"):
        return [tool, *args]
    return [tool, "nextest", *args]


def key_for(binary_id: str, name: str) -> str:
    # `mycrate::tests` is a binary id; "::" inside a key body would be read as
    # a separator by the store, so the key keeps the path shape.
    return f"nextest::{binary_id.replace('::', '/')}/{name}"


def raw_for(binary_id: str, name: str) -> str:
    return f"{binary_id}||{name}"


def split_raw(raw: str) -> tuple[str, str]:
    binary_id, _, name = raw.rpartition("||")
    return binary_id, name


def _suite_pairs(suite: dict) -> list[tuple[str, str]]:
    cases = suite.get("testcases") or {}
    if isinstance(cases, dict):
        items = list(cases.items())
    else:
        items = [(c.get("name"), c) for c in cases if isinstance(c, dict)]
    return [(name, info if isinstance(info, dict) else {}) for name, info in items]


def parse_list(doc: dict) -> list[tuple[str, str]]:
    """[(binary id, test name)] from a `cargo nextest list` document."""
    suites = doc.get("rust-suites")
    pairs: list[tuple[str, str]] = []
    if isinstance(suites, dict):
        for binary_id, suite in suites.items():
            if not isinstance(suite, dict):
                continue
            for name, info in _suite_pairs(suite):
                if name and not info.get("ignored"):
                    pairs.append((binary_id, name))
    elif isinstance(suites, list):
        for suite in suites:
            if not isinstance(suite, dict):
                continue
            binary_id = (suite.get("binary-id") or suite.get("binary_id")
                         or suite.get("id") or suite.get("binary-name"))
            if not binary_id:
                continue
            for name, info in _suite_pairs(suite):
                if name and not info.get("ignored"):
                    pairs.append((binary_id, name))
    else:
        raise RuntimeError(
            "cargo nextest list returned no `rust-suites`; got keys: "
            + ", ".join(sorted(doc)) + ". This nextest version may need "
            "`--message-format json` (0.9.30+) or `--list-type only`.")
    return pairs


def discover_tests(tool: str, root: str) -> list[tuple[str, str]]:
    proc = subprocess.run(_cargo_argv(tool, "list", "--message-format", "json"),
                          cwd=root, capture_output=True, text=True)
    text = proc.stdout.strip()
    start, end = text.find("{"), text.rfind("}")
    if proc.returncode != 0 or start < 0:
        raise RuntimeError("cargo nextest could not list the tests:\n"
                           + (proc.stdout + proc.stderr)[-2000:])
    try:
        doc = json.loads(text[start:end + 1])
    except ValueError as e:
        raise RuntimeError(f"cargo nextest list is not valid JSON ({e}):\n"
                           + text[-1500:])
    return parse_list(doc)


def discover(root: str, out_path: str) -> int:
    pairs = discover_tests(nextest_bin(), root)
    base.write_ids(out_path, [(raw_for(b, n), key_for(b, n)) for b, n in pairs])
    return 0


def _event_test(ev: dict) -> tuple[str, str] | None:
    """(binary id, name) for a test event, or None for anything else."""
    kind = ev.get("type")
    name = ev.get("name")
    if kind == "test":
        # pre-0.9.38: {"type":"test","event":"started|ok|failed|ignored"}
        if ev.get("event") in (None, "started", "ok", "failed", "ignored"):
            binary = ev.get("binary-id") or ev.get("binary_id") or ""
            return (binary, name) if name else None
        return None
    if not kind or not isinstance(kind, str) or not kind.startswith("test-"):
        return None
    if kind.startswith("test-retry"):
        return None                      # an intermediate attempt, not a verdict
    binary = ev.get("binary-id") or ev.get("binary_id") or ""
    return (binary, name) if name else None


def _verdict(ev: dict) -> str | None:
    kind = ev.get("type")
    if kind == "test":
        return {"ok": "passed", "failed": "failed", "ignored": "skipped"}.get(ev.get("event", ""))
    return {"test-passed": "passed", "test-failed": "failed",
            "test-skipped": "skipped"}.get(kind)


def _is_start(ev: dict) -> bool:
    kind = ev.get("type")
    return kind == "test-started" or (kind == "test" and ev.get("event") == "started")


def _filter_expression(targets: list[tuple[str, str]]) -> str:
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return " | ".join(f"binary_id(={q(b)}) & test(={q(n)})" for b, n in targets)


def run(root: str, ledger: str, tests_file: str | None) -> int:
    tool = nextest_bin()
    # Discovery is also the identity map: events are matched back to the ids
    # collect wrote, and older nextest versions omit the binary id in events.
    known = discover_tests(tool, root)
    by_name: dict[str, list[str]] = {}
    for binary, name in known:
        by_name.setdefault(name, []).append(binary)
    all_pairs = {(b, n) for b, n in known}

    done = base.done_ids(ledger)
    wanted = base.selection_ids(tests_file)
    if wanted is None:
        targets = [p for p in known if raw_for(*p) not in done]
    else:
        targets = [split_raw(raw) for raw in wanted]
        targets = [p for p in targets if raw_for(*p) not in done]

    written = 0
    if targets:
        args = _cargo_argv(tool, "run", "--message-format", "json")
        if len(targets) < len(all_pairs):
            args += ["-E", _filter_expression(targets)]
        target_set = {raw_for(*p) for p in targets}
        emitted: set[str] = set()
        proc = subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        tail: list[str] = []
        for line in proc.stdout:                      # type: ignore[union-attr]
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
            hit = _event_test(ev)
            if not hit:
                continue
            binary, name = hit
            if raw_for(binary, name) not in target_set:
                # Older events may carry no binary id: accept the name when it
                # is unambiguous, otherwise leave the row to the next event.
                candidates = by_name.get(name, [])
                if len(candidates) != 1:
                    continue
                binary = candidates[0]
            raw = raw_for(binary, name)
            if raw not in target_set:
                continue
            if _is_start(ev):
                base.start_row(ledger, key_for(binary, name), raw)
                continue
            status = _verdict(ev)
            if not status or raw in emitted:
                continue
            emitted.add(raw)
            ms = float(ev.get("running") or ev.get("exec_time") or 0.0) * 1000.0
            base.end_row(ledger, key_for(binary, name), raw, status, ms)
            written += 1
        rc = proc.wait()
        if rc not in (0, TEST_FAILED_EXIT) and written == 0:
            raise RuntimeError(f"cargo nextest failed with exit code {rc}:\n"
                               + "".join(tail)[-1500:])
    base.suite_end_row(ledger, written)
    return 0


def main(argv=None) -> int:
    return driver.main("nextest", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
