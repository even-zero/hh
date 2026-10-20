"""hh — the timeout a test suite actually needs.

The distribution and the `hh` command keep the product name; the importable
package is ``executor``.

hh measures a suite once and then hands out a budget: p90 of the measured
per-test durations, plus headroom. That is the entire product. The first run
records (it is the naive command plus a ledger); every run after that uses the
measurement, so the process watching the suite is not killed by a timeout that
was guessed too low.

Stdlib-only core: zero install, zero sockets, no data leaves the repo.

Public contract: the ``run_tests`` tool (executor/tool.py) and the CLI
(``hh plan|run|report|tool-spec``).
"""
__version__ = "2.3.0"
