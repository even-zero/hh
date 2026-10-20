"""Collect and execute pytest node IDs without replaying unrelated tests.

Collection is performed once at plan time (cached) and again on execution (as
pytest requires). A measured run passes the node ids it is going to run
directly, so pytest collects *those* items only instead of the whole tree; the
file-and-deselect path remains as the fallback when the argv would be too big
or when a manifest has gone stale. The live plugin writes checkpoint rows
during execution.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


# Passing node ids directly makes pytest collect only the selected items. On
# click that skips 31k deselected parametrizations — 0.7 s of a 6.5 s suite —
# so a *measured* run can be faster than the naive one it replaces. Beyond these
# caps the argv itself becomes the problem: collect the tree and deselect.
NODEID_MAX = 8000
NODEID_ARGV_BYTES = 600_000


class Selection:
    def __init__(self, output=None, selected=None, keys_out=None, ledger=None):
        self.output = output
        self.selected = selected
        self.keys_out = keys_out
        self.ledger = ledger

    def pytest_sessionfinish(self, session, exitstatus):
        # Completeness marker: the session ran its tests to the end (failures
        # are a normal end). Interrupted (2), internal error (3) and usage
        # error (4) sessions are *not* complete: a cold record pass only trusts
        # its ledger as a manifest when this row exists, and sealing a manifest
        # from an aborted run would silently drop every test it never reached.
        if self.ledger and exitstatus in (0, 1):
            from .base import suite_end_row
            suite_end_row(self.ledger, session.testscollected)

    def pytest_collection_modifyitems(self, items):
        if self.selected is not None:
            kept = [item for item in items if item.nodeid in self.selected]
            removed = [item for item in items if item.nodeid not in self.selected]
            items[:] = kept
            if removed:
                # Let plugins see the deselection, just as -k does.
                item_config = (kept or removed)[0].config
                item_config.hook.pytest_deselected(items=removed)

    def pytest_collection_finish(self, session):
        # Run after -m/-k/addopts and other collection modifiers. A marked
        # opt-in group can be present at collect-only but absent at execution.
        if self.output:
            with open(self.output, "w", encoding="utf-8") as f:
                for item in session.items:
                    f.write(json.dumps(item.nodeid) + "\n")
        if self.keys_out:
            # A cold record pass has no manifest yet: derive identities from the
            # final selection with the same collision rule the planner uses, and
            # hand them to the ledger plugin. The plugin reads HH_KEYS_FILE on
            # the first test, which is after this hook.
            from ..keys import unique_key_map
            raw = [item.nodeid for item in session.items]
            km = unique_key_map(raw, "pytest")
            with open(self.keys_out, "w", encoding="utf-8") as f:
                for nodeid in raw:
                    f.write(json.dumps({"raw_id": nodeid, "key": km[nodeid]}) + "\n")
            os.environ["HH_KEYS_FILE"] = self.keys_out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--collect-out")
    ap.add_argument("--tests-file")
    ap.add_argument("--deselect-only", action="store_true",
                    help="collect the tree and deselect the rest (fallback path)")
    args = ap.parse_args(argv)
    if bool(args.collect_out) == bool(args.ledger):
        ap.error("exactly one of --collect-out or --ledger is required")
    import pytest
    os.chdir(os.path.abspath(args.root))
    # An explicit root limits discovery to the requested tree while preserving
    # the repo's pytest.ini/pyproject collection configuration.
    opts = ["-p", "no:cacheprovider", "-q"]
    # The naive command may need flags of its own (e.g. --ignore=…); hh must run
    # the same suite, so it accepts them verbatim.
    extra = os.environ.get("HH_PYTEST_ARGS", "").split()
    opts += extra
    if args.collect_out:
        opts.append("--collect-only")
    else:
        os.environ["HH_LEDGER"] = args.ledger
        opts += ["-p", "hh.adapters.pytest_plugin"]
    selected = None
    if args.tests_file:
        with open(args.tests_file, encoding="utf-8") as f:
            selected = {entry["raw_id"]: entry["key"] for line in f
                        for entry in [json.loads(line)]}
        os.environ["HH_KEYS_FILE"] = args.tests_file
    # Recursive discovery over every conventional test root that exists. A repo
    # with both tests/ and test/ (or nested suites) must not lose a tree just
    # because one directory name was checked first.
    roots = [d for d in ("tests", "test") if os.path.isdir(d)] or ["."]
    if selected and not args.deselect_only:
        ids = list(selected)
        argv_bytes = sum(len(i) + 1 for i in ids)
        if len(ids) <= NODEID_MAX and argv_bytes <= NODEID_ARGV_BYTES:
            # Deselect-after-collect pays the whole collection cost (2.9 s of a
            # 4.6 s click suite) even for a 40-test tail. Direct node ids make
            # pytest collect only what will run.
            rc = int(pytest.main(opts + ids,
                                 plugins=[Selection(None, None, None, args.ledger)]))
            if rc not in (2, 4):
                return rc
            # 4 = usage error (an id no longer exists); 2 = interrupted by
            # collection errors (an id that used to be collectible is not).
            # Nothing trustworthy ran, and a second pytest.main() in the same
            # process inherits the broken session: retry in a fresh process
            # that collects the tree and deselects everything else.
            import subprocess
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))) + os.pathsep + env.get("PYTHONPATH", "")
            return int(subprocess.run(
                [sys.executable, "-m", "hh.adapters.pytest_stream", "--root",
                 args.root, "--ledger", args.ledger, "--tests-file",
                 args.tests_file, "--deselect-only"], env=env).returncode)
    # A cold record pass (no test list) discovers identities during its own
    # collection, so the ledger and the next plan agree on every key.
    keys_out = (args.ledger + ".keys.jsonl"
                if selected is None and args.ledger else None)
    rc = pytest.main(opts + roots,
                     plugins=[Selection(args.collect_out, selected, keys_out, args.ledger)])
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
