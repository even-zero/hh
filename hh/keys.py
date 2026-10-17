"""Test identity normalization (ARCHITECTURE §7.1).

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
    if len(parts) >= 3 and (parts[-2][:1].isupper() or parts[-2] == parts[-3]):
        parts.pop(-2)
    key = f"{runner}::{'/'.join(parts)}"
    if params:
        key += f" :: {params}"
    return key


def file_of(key: str) -> str:
    """File-level aggregate key for a test key (all but the last name segment)."""
    body = key.split("::", 1)[1].split(" :: ")[0]
    return body.rsplit("/", 1)[0] if "/" in body else body


def name_of(key: str) -> str:
    body = key.split("::", 1)[1].split(" :: ")[0]
    return body.rsplit("/", 1)[1] if "/" in body else body


def suite_signature(keys: list[str]) -> str:
    return _h("\n".join(sorted(keys)))
