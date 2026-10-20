"""Test identity normalization (README: "stable test identities").

Composite key = runner :: norm-path/name :: param-hash.  Normalization must be
stable across renames of line-noise (paths, line numbers) but strict enough that
fuzzy tiers, not the key itself, handle true renames.
"""
from __future__ import annotations

import hashlib
import re


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def normalize(runner: str, raw_id: str) -> str:
    """Normalize a runner-native test id into a stable composite key.

    unittest id:  test_s001.TestS001.test_t000        -> unittest::test_s001/test_t000
    pytest nodeid: tests/a/test_b.py::C::test_x[p-1]  -> pytest::tests/a/test_b/test_x :: p|1
    junit id:     com.example.FooTest.testAdd[#1]     -> junit::com/example/FooTest/testAdd :: <hash>

    In Java the class *is* the file, so its segment is kept; dropping it (the
    unittest/pytest rule below, where a class rename must not break identity)
    would collapse every same-named method in a package into one key.
    """
    raw = raw_id.strip()
    params = ""
    if "[" in raw and raw.endswith("]"):
        raw, params = raw[: raw.rindex("[")], raw[raw.rindex("[") + 1 : -1]
        params = _h(params)
    raw = raw.replace("::", "/").replace(".", "/").replace("\\", "/")
    raw = re.sub(r"/+", "/", raw).strip("/")
    # drop class-level segments only when they duplicate the file stem pattern
    parts = raw.split("/")
    # drop class-level segment (unittest Class.method, pytest ::Class::test_x):
    # class renames must not break identity — that is what fuzzy tiers are for
    if (runner != "junit" and len(parts) >= 3
            and (parts[-2][:1].isupper() or parts[-2] == parts[-3])):
        parts.pop(-2)
    key = f"{runner}::{'/'.join(parts)}"
    if params:
        key += f" :: {params}"
    return key


def unique_key_map(raw_ids: list[str], runner: str) -> dict[str, str]:
    """raw test id -> key, disambiguating keys that normalization collapses.

    Normalization drops the class segment on purpose (class renames must not
    break identity), so ``TestA.test_init`` and ``TestB.test_init`` in one file
    share a key. Both are runnable, so both must stay addressable: the key gets
    a deterministic suffix derived from the raw id. Discovery and execution use
    this one function, so they can never disagree about identities.
    """
    counts: dict[str, int] = {}
    for raw in raw_ids:
        k = normalize(runner, raw)
        counts[k] = counts.get(k, 0) + 1
    out: dict[str, str] = {}
    for raw in raw_ids:
        k = normalize(runner, raw)
        if counts[k] > 1:
            k += " :: collision-" + hashlib.sha1(raw.encode()).hexdigest()[:12]
        out[raw] = k
    return out


def file_of(key: str) -> str:
    """File-level aggregate key for a test key (all but the last name segment)."""
    body = key.split("::", 1)[1].split(" :: ")[0]
    return body.rsplit("/", 1)[0] if "/" in body else body


def name_of(key: str) -> str:
    body = key.split("::", 1)[1].split(" :: ")[0]
    return body.rsplit("/", 1)[1] if "/" in body else body


def suite_signature(keys: list[str]) -> str:
    return _h("\n".join(sorted(keys)))
