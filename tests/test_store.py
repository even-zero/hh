"""Store behaviour that makes resume safe: offset-aware ingest never double-counts."""
import json
import os
import shutil
import tempfile
import time
import unittest

from hh.store import Store


def end_row(key: str, ms: float, status: str = "passed") -> str:
    return json.dumps({"type": "end", "key": key, "raw_id": key.split("::", 1)[1],
                       "status": status, "duration_ms": ms, "censored": False,
                       "ts": time.time()})


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h8-store-")
        self.ledger = os.path.join(self.tmp, "ledger.jsonl")
        self.store = Store(self.tmp)
        self.run_id = self.store.start_run("unittest", suite_sig=None)
        self.ctx = self.store.ctx_id(workers=1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append(self, *lines):
        with open(self.ledger, "a", encoding="utf-8") as f:
            for l in lines:
                f.write(l + "\n")

    def test_ingests_only_end_rows(self):
        self._append(json.dumps({"type": "start", "key": "unittest::a/b"}),
                     end_row("unittest::a/b", 12.5))
        n = self.store.ingest_ledger(self.ledger, self.run_id, self.ctx, "unittest")
        self.assertEqual(n, 1)
        self.store.refresh_aggregates()
        self.assertEqual(len(self.store.agg_map("test")), 1)

    def test_offset_ingest_is_idempotent(self):
        self._append(end_row("unittest::a/b", 10.0))
        size1 = os.path.getsize(self.ledger)
        n1 = self.store.ingest_ledger(self.ledger, self.run_id, self.ctx, "unittest")
        n2 = self.store.ingest_ledger(self.ledger, self.run_id, self.ctx, "unittest",
                                      offset=size1)
        self._append(end_row("unittest::a/c", 20.0))
        n3 = self.store.ingest_ledger(self.ledger, self.run_id, self.ctx, "unittest",
                                      offset=size1)
        self.assertEqual((n1, n2, n3), (1, 0, 1))
        rows = self.store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        self.assertEqual(rows, 2, "resumed runs must not re-ingest the same rows")

    def test_censoring_is_preserved(self):
        self._append(end_row("unittest::a/hang", 2000.0, status="timeout").replace(
            '"censored": false', '"censored": true'))
        self.store.ingest_ledger(self.ledger, self.run_id, self.ctx, "unittest")
        self.store.refresh_aggregates()
        self.assertEqual(self.store.censored_keys(1), ["unittest::a/hang"])


class TestSchema(unittest.TestCase):
    def test_savings_table_exists(self):
        tmp = tempfile.mkdtemp(prefix="h8-schema-")
        try:
            st = Store(tmp)
            st.db.execute("SELECT kind, amount_ms FROM savings").fetchall()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class TestRunnerOverhead(unittest.TestCase):
    """Per-test runner overhead must come from clean single-attempt runs only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h8-overhead-")
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *, wall_ms: float, test_ms: float, n: int = 2,
             collection_ms: float = 100.0, attempts: int = 1) -> None:
        rid = self.store.start_run("pytest", suite_sig=None)
        ctx = self.store.ctx_id(workers=1)
        for i in range(n):
            self.store.add_observation(rid, ctx, runner="pytest",
                                       test_key=f"pytest::t/t{i}", raw_id=f"t{i}",
                                       status="passed", duration_ms=test_ms,
                                       file_path="t", name=f"t{i}")
        self.store.commit()
        self.store.finish_run(rid, total_ms=test_ms * n, wall_ms=wall_ms,
                              collection_ms=collection_ms, attempts=attempts)

    def test_single_attempt_run_gives_the_rate(self):
        # 2 tests x 250 ms + 100 ms startup + 100 ms of plumbing over 2 tests
        self._run(wall_ms=700.0, test_ms=250.0)
        self.assertAlmostEqual(self.store.runner_overhead_ms("pytest"), 50.0, places=3)

    def test_killed_or_resumed_run_is_not_overhead(self):
        # a retry adds 30 s of wall clock that no ledger row explains: counting
        # it would charge 15 s/test of phantom "runner overhead"
        self._run(wall_ms=700.0, test_ms=250.0)
        self._run(wall_ms=30700.0, test_ms=250.0, attempts=2)
        self.assertAlmostEqual(self.store.runner_overhead_ms("pytest"), 50.0, places=3)

    def test_cold_store_has_no_overhead_guess(self):
        self.assertEqual(self.store.runner_overhead_ms("pytest"), 0.0)
