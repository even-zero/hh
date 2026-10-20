"""Run a Rust test suite through cargo-nextest.

nextest is a runner, not just a reporter: it has a stable machine-readable
listing and a machine-readable event stream, which is exactly the pair hh needs.

  * discovery: `cargo nextest list --message-format json` — the binaries and
    their test cases, without running them. Ignored tests are left out: nextest
    does not run them either, so recording them would only create ids that
    never resolve.
  * execution: `cargo nextest run --message-format json`, which emits the
    **libtest** line format (one JSON object per test event):

        {"type":"suite","event":"started","test_count":2}
        {"type":"test","event":"started","name":"demo::demo$tests::test_add"}
        {"type":"test","event":"ok","name":"demo::demo$tests::test_add",
         "exec_time":0.0001}
        {"type":"suite","event":"ok","passed":2,"failed":0,"ignored":0,
         "measured":0,"filtered_out":0,"exec_time":0.002}

    nextest prefixes the test name with `<package>::<binary-name>$` so a
    workspace with many binaries stays unambiguous; the suffix is the test name
    as `nextest list` reports it. Rows are written as events arrive, so a kill
    at the budget keeps every finished test.

Granularity is the test. A resume passes nextest a filter expression
(`-E 'binary_id(="…") & test(="…")'`) so only the tests still missing run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from . import base, driver

TEST_FAILED_EXIT = 100
NO_TESTS_RUN_EXIT = 4

# The filter expression goes on the command line, so a resume of a very large
# suite is split into several runs (one per group) instead of one huge argv.
MAX_FILTER_CHARS = 40_000

_RETRY_SUFFIX = re.compile(r"#\d+$")
_STRESS_SUFFIX = re.compile(r"@stress-\d+$")
_VERDICTS = {"ok": "passed", "failed": "failed", "ignored": "skipped"}


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
    # Both halves use `::` (a binary id like `demo::tests`, a module path like
    # `tests::test_add`); in a key that shape becomes a path, so the file tier
    # is the package and nothing has to guess where the name ends.
    return f"nextest::{binary_id.replace('::', '/')}/{name.replace('::', '/')}"


def raw_for(binary_id: str, name: str) -> str:
    return f"{binary_id}||{name}"


def split_raw(raw: str) -> tuple[str, str]:
    binary_id, _, name = raw.rpartition("||")
    return binary_id, name


def parse_suites(doc: dict) -> list[dict]:
    """[{binary_id, package, binary, tests: [(name, ignored)]}] from a list document.

    The schema is nextest-metadata's `TestListSummary`:

        {"test-count": 2,
         "rust-suites": {"demo": {"package-name": "demo",
                                  "binary-name": "demo",
                                  "binary-id": "demo",
                                  "kind": "lib",
                                  "testcases": {"tests::test_add": {"ignored": false}}}}}
    """
    suites = doc.get("rust-suites")
    if not isinstance(suites, dict):
        raise RuntimeError(
            "cargo nextest list returned no `rust-suites` object; got keys: "
            + ", ".join(sorted(doc)) + ". This needs `--message-format json`.")
    out: list[dict] = []
    for binary_id, suite in suites.items():
        if not isinstance(suite, dict):
            continue
        if suite.get("status", "listed") != "listed":
            # listed in the build but not runnable here (e.g. unsupported target)
            continue
        cases = suite.get("testcases") or {}
        if isinstance(cases, dict):
            items = list(cases.items())
        else:
            items = [(c.get("name"), c) for c in cases if isinstance(c, dict)]
        tests = [(name, bool(info.get("ignored")))
                 for name, info in items
                 if name and isinstance(info, dict)]
        out.append({"binary_id": suite.get("binary-id") or binary_id,
                    "package": suite.get("package-name") or "",
                    "binary": suite.get("binary-name") or "",
                    "tests": tests})
    return out


def parse_list(doc: dict) -> list[tuple[str, str]]:
    """[(binary id, test name)] from a `cargo nextest list` document."""
    pairs: list[tuple[str, str]] = []
    for suite in parse_suites(doc):
        for name, ignored in suite["tests"]:
            if not ignored:
                pairs.append((suite["binary_id"], name))
    return pairs


def _load_document(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError("cargo nextest list printed no JSON:\n" + text[-1500:])
    try:
        return json.loads(text[start:end + 1])
    except ValueError as e:
        raise RuntimeError(f"cargo nextest list is not valid JSON ({e}):\n"
                           + text[-1500:])


def discover_tests(tool: str, root: str) -> list[dict]:
    proc = subprocess.run(_cargo_argv(tool, "list", "--message-format", "json"),
                          cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("cargo nextest could not list the tests:\n"
                           + (proc.stdout + proc.stderr)[-2000:])
    return parse_suites(_load_document(proc.stdout))


def discover(root: str, out_path: str) -> int:
    suites = discover_tests(nextest_bin(), root)
    base.write_ids(out_path,
                   [(raw_for(s["binary_id"], name), key_for(s["binary_id"], name))
                    for s in suites for name, ignored in s["tests"] if not ignored])
    return 0


class Identity:
    """Maps a libtest event name back to the ids `list` produced.

    Events name a test `<package>::<binary-name>$<test-name>`. Both halves come
    from the same list document, so the mapping is exact; the name-only fallback
    covers older nextest versions, which emitted the bare test name.
    """

    def __init__(self, suites: list[dict]):
        self.by_prefix: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = {}
        self.pairs: set[tuple[str, str]] = set()
        for suite in suites:
            binary = suite["binary_id"]
            if suite["package"] and suite["binary"]:
                self.by_prefix[f"{suite['package']}::{suite['binary']}"] = binary
            for name, ignored in suite["tests"]:
                if ignored:
                    continue
                self.by_name.setdefault(name, []).append(binary)
                self.pairs.add((binary, name))

    def resolve(self, name: str) -> tuple[tuple[str, str] | None, str]:
        """((binary id, test name) or None, test name)."""
        name = _RETRY_SUFFIX.sub("", name)
        if "$" not in name:
            return self._by_name(name), name
        prefix, _, test = name.rpartition("$")
        prefix = _STRESS_SUFFIX.sub("", prefix)
        binary = self.by_prefix.get(prefix)
        if binary and (binary, test) in self.pairs:
            return (binary, test), test
        return self._by_name(test), test

    def _by_name(self, test: str) -> tuple[str, str] | None:
        binaries = self.by_name.get(test) or []
        return (binaries[0], test) if len(binaries) == 1 else None


def parse_event(ev: dict) -> tuple[str, str] | None:
    """(kind, name) for a test event: kind is `started`, `ok`, `failed`, `ignored`."""
    if ev.get("type") != "test":
        return None
    event, name = ev.get("event"), ev.get("name")
    if event not in ("started", "ok", "failed", "ignored") or not name:
        return None
    return event, name


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _filter_expression(targets: list[tuple[str, str]]) -> str:
    return " | ".join(f"(binary_id(={_quote(b)}) & test(={_quote(n)}))"
                      for b, n in targets)


def _groups(targets: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    groups: list[list[tuple[str, str]]] = [[]]
    size = 0
    for target in targets:
        cost = len(_filter_expression([target])) + 3
        if groups[-1] and size + cost > MAX_FILTER_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(target)
        size += cost
    return [g for g in groups if g]


def _stream_run(tool: str, root: str, targets: list[tuple[str, str]],
                identity: Identity, ledger: str, filtered: bool) -> int:
    args = _cargo_argv(tool, "run", "--message-format", "json")
    if filtered:
        args += ["-E", _filter_expression(targets)]
    wanted = {raw_for(*t) for t in targets}
    proc = subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail: list[str] = []
    written = 0
    started: set[str] = set()
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
        hit = parse_event(ev)
        if not hit:
            continue
        event, raw_name = hit
        resolved, test = identity.resolve(raw_name)
        if not resolved:
            continue
        binary, name = resolved
        raw = raw_for(binary, name)
        if raw not in wanted:
            # A looser filter may run more than hh asked for; those rows are
            # not ours to record. (A selection is grouped, never widened.)
            continue
        key = key_for(binary, name)
        if event == "started":
            if raw not in started:
                started.add(raw)
                base.start_row(ledger, key, raw)
            continue
        status = _VERDICTS[event]
        ms = float(ev.get("exec_time") or 0.0) * 1000.0
        base.end_row(ledger, key, raw, status, ms, censored=False)
        written += 1
    rc = proc.wait()
    if rc == NO_TESTS_RUN_EXIT:
        raise RuntimeError(
            "cargo nextest ran no tests for a filter built from %d test id(s); "
            "the ids no longer match the suite" % len(targets))
    if rc not in (0, TEST_FAILED_EXIT) and written == 0:
        raise RuntimeError(f"cargo nextest failed with exit code {rc}:\n"
                           + "".join(tail)[-1500:])
    return written


def run(root: str, ledger: str, tests_file: str | None) -> int:
    tool = nextest_bin()
    # Discovery is also the identity map: events name tests, ids key rows.
    suites = discover_tests(tool, root)
    identity = Identity(suites)

    done = base.done_ids(ledger)
    wanted = base.selection_ids(tests_file)
    if wanted is None:
        targets = [p for p in sorted(identity.pairs) if raw_for(*p) not in done]
    else:
        targets = [p for p in (split_raw(raw) for raw in wanted)
                   if p in identity.pairs and raw_for(*p) not in done]

    written = 0
    for group in _groups(targets):
        written += _stream_run(tool, root, group, identity, ledger,
                               filtered=len(group) < len(identity.pairs))
    base.suite_end_row(ledger, written)
    return 0


def main(argv=None) -> int:
    return driver.main("nextest", discover, run, argv)


if __name__ == "__main__":
    sys.exit(main())
