"""Self-overhead meter: how long hh spent on its own work.

The meter wraps hh's code paths (collection, ledger writes, ingest,
aggregation). A plan reports the total as ``overhead_ms``, so the tool's own
cost is a number the caller can see rather than an excuse.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Meter:
    spans_ms: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @contextmanager
    def span(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            self.spans_ms[name] = self.spans_ms.get(name, 0.0) + dt
            self.counts[name] = self.counts.get(name, 0) + 1

    @property
    def total_ms(self) -> float:
        return sum(self.spans_ms.values())

    def snapshot(self) -> dict:
        return {
            "overhead_ms": round(self.total_ms, 3),
            "spans": {k: round(v, 3) for k, v in sorted(self.spans_ms.items())},
            "counts": dict(self.counts),
        }


# Process-global meter: adapters in child processes use the same API and write
# their totals into the ledger's run-summary row so parents can aggregate them.
GLOBAL = Meter()
