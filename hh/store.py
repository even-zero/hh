"""Duration store: repo-local SQLite (ARCHITECTURE §6).

Raw observations are append-only and gitignored; aggregates are materialized and
committable. Aggregates can always be rebuilt from raw rows when the estimator
is re-tuned.
"""
from __future__ import annotations

import json
import math
import os
import platform
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, runner TEXT, git_sha TEXT, suite_sig TEXT,
  collection_ms REAL, total_ms REAL, wall_ms REAL, started_at REAL
);
CREATE TABLE IF NOT EXISTS contexts (
  id INTEGER PRIMARY KEY, machine_fp TEXT, workers INTEGER, runner_ver TEXT, extras JSON
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL, runner TEXT NOT NULL, test_key TEXT NOT NULL,
  raw_id TEXT NOT NULL, file_path TEXT, name TEXT, param_hash TEXT, content_hash TEXT,
  status TEXT NOT NULL, duration_ms REAL NOT NULL,
  censored INTEGER NOT NULL DEFAULT 0, ctx_id INTEGER NOT NULL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS obs_key ON observations(test_key);
CREATE INDEX IF NOT EXISTS obs_file ON observations(file_path);
CREATE TABLE IF NOT EXISTS aggregates (
  level TEXT NOT NULL, level_key TEXT NOT NULL,
  n INTEGER, n_censored INTEGER, p50 REAL, p90 REAL, mean_ewma REAL, mad REAL,
  mean REAL,
  updated_at REAL, PRIMARY KEY (level, level_key)
);
CREATE TABLE IF NOT EXISTS savings (
  id INTEGER PRIMARY KEY, ts REAL, job_id TEXT, suite_id TEXT, kind TEXT,
  amount_ms REAL, basis TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY, ts REAL, policy TEXT, suite_id TEXT, attempt INTEGER,
  budget_s REAL, outcome TEXT, wall_ms REAL, wasted_ms REAL, overhead_ms REAL, note TEXT
);
"""


def machine_fp() -> str:
    try:
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024**3)
    except (ValueError, OSError):
        mem = 0
    return f"{platform.machine()}-cpu{os.cpu_count()}-mem{mem}g"


class Store:
    def __init__(self, root: str):
        self.root = os.path.join(root, ".hh")
        os.makedirs(self.root, exist_ok=True)
        self.path = os.path.join(self.root, "observations.sqlite")
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        # migration: stores created before `mean` existed (aggregates are
        # rebuildable, so an added column plus a refresh is enough)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(aggregates)")}
        if "mean" not in cols:
            self.db.execute("ALTER TABLE aggregates ADD COLUMN mean REAL")
        self.db.commit()

    # -- runs / contexts -------------------------------------------------
    def start_run(self, runner: str, suite_sig: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO runs(runner, suite_sig, started_at) VALUES (?,?,?)",
            (runner, suite_sig, time.time()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, total_ms: float, wall_ms: float,
                   collection_ms: float | None = None) -> None:
        self.db.execute(
            "UPDATE runs SET total_ms=?, wall_ms=?, collection_ms=? WHERE id=?",
            (total_ms, wall_ms, collection_ms, run_id),
        )
        self.db.commit()

    def runner_overhead_ms(self, runner: str, limit: int = 5) -> float:
        """Measured per-test runner bookkeeping (framework plumbing).

        For each recent completed run: (wall − collection − Σ test durations) /
        number of tests recorded by that run; median over the last `limit`.
        Returns 0.0 when the store has no completed run — a cold store gets no
        guess, it gets a record pass.
        """
        rows = self.db.execute(
            "SELECT r.id, r.wall_ms, COALESCE(r.collection_ms, 0.0),"
            " COUNT(o.id), SUM(o.duration_ms)"
            " FROM runs r JOIN observations o ON o.run_id = r.id"
            " WHERE r.runner=? AND r.wall_ms > 0 AND r.collection_ms IS NOT NULL"
            " GROUP BY r.id ORDER BY r.id DESC LIMIT ?", (runner, limit)).fetchall()
        vals = []
        for _id, wall, coll, n, total in rows:
            if not n:
                continue
            over = ((wall or 0.0) - (coll or 0.0) - (total or 0.0)) / n
            vals.append(max(0.0, over))
        if not vals:
            return 0.0
        vals.sort()
        return round(vals[len(vals) // 2], 3)

    def ctx_id(self, workers: int = 1, runner_ver: str | None = None) -> int:
        fp = machine_fp()
        row = self.db.execute(
            "SELECT id FROM contexts WHERE machine_fp=? AND workers=?",
            (fp, workers),
        ).fetchone()
        if row:
            return int(row[0])
        cur = self.db.execute(
            "INSERT INTO contexts(machine_fp, workers, runner_ver, extras) VALUES (?,?,?,?)",
            (fp, workers, runner_ver, json.dumps({})),
        )
        self.db.commit()
        return int(cur.lastrowid)

    # -- observations ----------------------------------------------------
    def add_observation(self, run_id: int, ctx_id: int, *, runner: str, test_key: str,
                        raw_id: str, status: str, duration_ms: float,
                        censored: bool = False, file_path: str | None = None,
                        name: str | None = None, ts: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO observations(run_id, runner, test_key, raw_id, file_path, name,"
            " status, duration_ms, censored, ctx_id, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, runner, test_key, raw_id, file_path, name, status,
             duration_ms, int(censored), ctx_id, ts or time.time()),
        )

    def commit(self) -> None:
        self.db.commit()

    def ingest_ledger(self, ledger_path: str, run_id: int, ctx_id: int, runner: str,
                      offset: int = 0) -> int:
        """Import end-rows from a run ledger (optionally from a byte offset, so a
        resumed run re-ingests nothing). Returns count ingested."""
        from .keys import file_of, name_of

        n = 0
        if not os.path.exists(ledger_path):
            # An adapter that died before its first row leaves no ledger. That
            # is a shard failure the caller reports, not an ingest crash.
            return 0
        with open(ledger_path, "r", encoding="utf-8") as f:
            if offset:
                f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("type") != "end":
                    continue
                key = row["key"]
                self.add_observation(
                    run_id, ctx_id, runner=runner, test_key=key,
                    raw_id=row.get("raw_id", key), status=row["status"],
                    duration_ms=float(row["duration_ms"]),
                    censored=bool(row.get("censored", False)),
                    file_path=file_of(key), name=name_of(key), ts=row.get("ts"),
                )
                n += 1
        self.db.commit()
        return n

    # -- aggregates ------------------------------------------------------
    def refresh_aggregates(self) -> None:
        """Incremental refresh of test/file/global aggregates.

        Robust stats: p50/p90 empirical, EWMA mean (alpha=.3) for recency,
        MAD for spread. Censored rows count as lower bounds (kept as-is).
        """
        rows = self.db.execute(
            "SELECT test_key, file_path, duration_ms, censored, ts FROM observations"
            " ORDER BY ts"
        ).fetchall()
        by_test: dict[str, list[tuple[float, int]]] = {}
        by_file: dict[str, list[tuple[float, int]]] = {}
        glob: list[tuple[float, int]] = []
        for key, fpath, dur, cens, _ts in rows:
            by_test.setdefault(key, []).append((dur, cens))
            by_file.setdefault(fpath or "?", []).append((dur, cens))
            glob.append((dur, cens))
        now = time.time()
        self.db.execute("DELETE FROM aggregates")
        for level, table in (("test", by_test), ("file", by_file), ("global", {"global": glob})):
            for k, vals in table.items():
                self._agg_row(level, k, vals, now)
        self.db.commit()

    def _agg_row(self, level: str, key: str, vals: list[tuple[float, int]], now: float) -> None:
        durs = sorted(v for v, _ in vals)
        if not durs:
            return
        n = len(durs)
        p50 = durs[n // 2]
        p90 = durs[min(n - 1, int(0.9 * n))]
        ewma = durs[0]
        for v in durs[1:]:
            ewma = 0.3 * v + 0.7 * ewma
        med = p50
        mad = sorted(abs(v - med) for v in durs)[n // 2]
        # arithmetic mean: the *mass* estimator. The median is robust but for
        # heavy-tailed suites (a few 1-2 s CLI tests among thousands of 1 ms
        # ones) summing medians under-predicts the suite's true duration. The
        # mean is what "total test mass" actually means.
        mean = sum(durs) / n
        self.db.execute(
            "INSERT OR REPLACE INTO aggregates(level, level_key, n, n_censored,"
            " p50, p90, mean_ewma, mad, mean, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (level, key, n, sum(1 for _, c in vals if c), p50, p90, ewma, mad, mean, now),
        )

    # -- lookups ---------------------------------------------------------
    def agg_map(self, level: str) -> dict[str, dict]:
        out = {}
        for key, n, nc, p50, p90, ewma, mad, mean in self.db.execute(
            "SELECT level_key, n, n_censored, p50, p90, mean_ewma, mad, mean"
            " FROM aggregates WHERE level=?", (level,)
        ):
            out[key] = {"n": n, "n_censored": nc, "p50": p50, "p90": p90,
                        "mean_ewma": ewma, "mad": mad, "mean": mean}
        return out

    def censored_keys(self, min_count: int = 2) -> list[str]:
        """Keys censored >= min_count times ⇒ hang suspects (§7.6)."""
        return [
            r[0]
            for r in self.db.execute(
                "SELECT test_key, SUM(censored) c FROM observations"
                " GROUP BY test_key HAVING c >= ?", (min_count,)
            )
        ]

    def suite_total_history(self, suite_sig: str) -> list[float]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT total_ms FROM runs WHERE suite_sig=? AND total_ms IS NOT NULL"
                " ORDER BY started_at DESC LIMIT 20", (suite_sig,)
            )
        ]

    # -- attempts (experiment accounting) --------------------------------
    def log_attempt(self, **kw) -> None:
        self.db.execute(
            "INSERT INTO attempts(ts, policy, suite_id, attempt, budget_s, outcome,"
            " wall_ms, wasted_ms, overhead_ms, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kw.get("ts", time.time()), kw.get("policy"), kw.get("suite_id"),
             kw.get("attempt"), kw.get("budget_s"), kw.get("outcome"),
             kw.get("wall_ms"), kw.get("wasted_ms"), kw.get("overhead_ms"),
             kw.get("note")),
        )
        self.db.commit()
