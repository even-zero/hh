"""The runner registry: one place that knows how to launch each adapter.

An adapter is one of two kinds:

  module  a Python module that speaks the CLI
          ``--root DIR --collect-out FILE`` / ``--root DIR --ledger FILE
          [--tests-file FILE]`` (unittest, pytest, junit)

  node    a .cjs script run as ``node <script> collect|run ...``
          (mocha, jest, vitest, node:test, tap)

Adding a runner means adding one line here plus its page in docs/.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "js")

MODULE_ADAPTERS = {
    "unittest": "executor.adapters.unittest_stream",
    "pytest": "executor.adapters.pytest_stream",
    "junit": "executor.adapters.junit_stream",
}

NODE_ADAPTERS = {
    "node": "mocha_stream.cjs",
    "jest": "jest_stream.cjs",
    "vitest": "vitest_stream.cjs",
    "node-test": "nodetest_stream.cjs",
    "tap": "tap_stream.cjs",
}

RUNNERS = tuple(MODULE_ADAPTERS) + tuple(NODE_ADAPTERS)


def is_node(runner: str) -> bool:
    return runner in NODE_ADAPTERS


def node_script(runner: str) -> str:
    return os.path.join(JS, NODE_ADAPTERS[runner])


def module_name(runner: str) -> str:
    return MODULE_ADAPTERS[runner]
