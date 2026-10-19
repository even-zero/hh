"""Suite shims and hang injection.

`unittest_exec` lane repos whose discovery/naming doesn't match hh's unittest
manifest get a *compat shim*: one wrapper module per real test module that
re-exports only the TestCase classes DEFINED in that module, named test_*.py.
Both arms (naive and hh) run against the same shim directory, so the measured
universe is identical; the shim itself is part of the methodology, not a patch
to the suite. (When hh's manifest learns native patterns/pytest, the lane can
be retired without touching repos.json entries beyond flipping the lane key.)
"""
from __future__ import annotations

import os
import sys

from . import config

CODE_KEYS = {
    "unittest_while_true": """
import time, unittest

class InjectedHang{i}(unittest.TestCase):
    def test_hang_forever_{i}(self):
        while True:
            time.sleep(0.5)
""",
    "pytest_while_true": '''"""INJECTED by hh/benchmark: a regression test that never finishes."""
import time


def test_never_finishes():
    while True:
        time.sleep(0.5)
''',
    "mocha_while_true": """// INJECTED by hh/benchmark: a regression test that never finishes.
describe('injected hang', function () {
  this.timeout(0);
  it('never finishes', function (done) { setInterval(() => {}, 500); });
});
""",
}

WRAPPER = """import unittest as _u
import {mod} as _m
for _n in dir(_m):
    _v = getattr(_m, _n)
    if isinstance(_v, type) and issubclass(_v, _u.TestCase) and _v.__module__ == {mod!r}:
        globals()[_n] = _v
"""


def shim_dir(repo: dict, hang: bool = False) -> str:
    spec = repo["shim"]
    if hang:
        base = spec["hang"]["name"]
    else:
        base = spec["suite_name"]
    return os.path.join(config.WORK, "shims", base)


def build_shim(repo: dict) -> dict:
    """Create <suite_name> and (if hang.enabled) <hang.name> shim dirs."""
    spec = repo["shim"]
    src = os.path.join(config.repo_dir(repo), spec["package_root"])
    out = {}
    sys_path_added = config.repo_dir(repo)
    sys.path.insert(0, sys_path_added)
    try:
        mods = sorted(f for f in os.listdir(src) if f.endswith(spec["glob"].lstrip("*")))
        for hang in (False, True):
            if hang and not spec.get("hang", {}).get("enabled"):
                continue
            dst = shim_dir(repo, hang)
            os.makedirs(dst, exist_ok=True)
            skipped, written = [], 0
            for f in mods:
                mod = spec["package_root"].replace("/", ".") + "." + f[:-3]
                wrapper = os.path.join(dst, "test_" + f[:-len(spec["glob"].lstrip("*"))] + ".py")
                try:
                    __import__(mod)
                    body = WRAPPER.format(mod=mod)
                    written += 1
                except Exception as e:  # optional dep missing: empty wrapper, recorded
                    body = f"# import skipped by hh/benchmark shim: {e!r}\n"
                    skipped.append({"module": mod, "error": repr(e)})
                with open(wrapper, "w") as fh:
                    fh.write(body)
            if hang:
                hs = spec["hang"]
                for i in range(hs["count"]):
                    with open(os.path.join(dst, f"test_injhang{i}.py"), "w") as fh:
                        fh.write(CODE_KEYS[hs["code_key"]].format(i=i))
            out["hang" if hang else "clean"] = {
                "dir": dst, "modules": len(mods), "wrappers_with_tests": written,
                "skipped_imports": skipped,
                "hangs_injected": spec["hang"]["count"] if hang else 0}
    finally:
        if sys.path and sys.path[0] == sys_path_added:
            sys.path.pop(0)
    return out


def inject_hangs(repo_dir: str, injections: list[dict], code_keys: dict = CODE_KEYS) -> list[str]:
    paths = []
    for inj in injections:
        p = os.path.join(repo_dir, inj["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(code_keys[inj["code_key"]].format(i=0) if "{i}" in code_keys[inj["code_key"]] else code_keys[inj["code_key"]])
        paths.append(p)
    return paths
