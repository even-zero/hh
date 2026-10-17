"""Self-overhead meter (Law 4). Every plan reports overhead_ms; this is how.

The meter wraps hh's own code paths (plan, ledger writes, ingest, aggregation).
It is the primary bound-check for the §8.1 guarantee; wall-clock diffs in
experiments are the secondary, noisier check.
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
