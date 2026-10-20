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
    def test_schema_is_the_three_tables_it_needs(self):
        tmp = tempfile.mkdtemp(prefix="h8-schema-")
        try:
            st = Store(tmp)
            tables = {r[0] for r in st.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"runs", "observations", "aggregates"} <= tables)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_durations_are_read_back_in_time_order(self):
        tmp = tempfile.mkdtemp(prefix="h8-dur-")
        try:
            st = Store(tmp)
            rid = st.start_run("pytest")
            ctx = st.ctx_id()
            for i, ms in enumerate((10.0, 20.0, 30.0)):
                st.add_observation(rid, ctx, runner="pytest", test_key="pytest::t/a",
                                   raw_id="t/a", status="passed", duration_ms=ms,
                                   file_path="t", name="a", ts=1000.0 + i)
            self.assertEqual(st.durations("pytest::t/a"), [10.0, 20.0, 30.0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


