"""The estimator: measured durations in, one timeout out.

hh answers exactly one question — *how long does this suite take?* — so that the
process watching it can set a timeout that does not kill it. That is the whole
model:

    budget = p90(measured tests) x safety

The p90 comes from the rows this repo has recorded. On the first run there are
no rows, so there is no estimate: the run is executed like the naive command
and recorded (`policy.record_plan`). From the second run on, every number below
is a measurement from this store:

  T0  the test's own rows        (median / EWMA blend, MAD-derived spread)
  T1  the test's file rows       (mean of that file's measurements)
  T2  the whole suite's rows     (mean of every measurement)
  --  nothing measured           (no estimate: record first)

There are no bundled priors and no sampling. A prediction that was not measured
in this repository does not exist in this program.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .keys import file_of

MATCHED_SIGMA_FLOOR = 0.25     # even known tests vary a bit run to run
EXTRAPOLATED_SIGMA = 0.60      # a test extrapolated from its file/suite rows


@dataclass
class TestPred:
    key: str
    median: float            # point estimate of this test's duration (ms)
    sigma_log: float         # log-normal spread
    matched: bool            # this test's own rows were used


@dataclass
class Estimate:
    p50: float
    p90: float
    p99: float
    coverage: float                       # measured mass / total mass
    budget_s: float = 0.0                 # p90 x safety, what the plan uses
    preds: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
    calibration: dict = field(default_factory=dict)


def _location(vals: list[float]) -> float:
    """Robust center: half median, half EWMA (recent runs weigh more)."""
    s = sorted(vals)
    n = len(s)
    med = s[n // 2]
    ewma = s[0]
    for v in s[1:]:
        ewma = 0.3 * v + 0.7 * ewma
    return 0.5 * med + 0.5 * ewma


def _sigma_log(vals: list[float], median: float) -> float:
    if len(vals) < 2 or median <= 0:
        return MATCHED_SIGMA_FLOOR
    devs = [abs(math.log(max(v, 1e-3) / median)) for v in vals]
    sig = 1.4826 * sorted(devs)[len(devs) // 2]
    return min(max(sig, MATCHED_SIGMA_FLOOR), 1.5)


def predict_tests(keys: list[str], store) -> tuple[list[TestPred], dict]:
    agg = store.agg_map("test")
    files = store.agg_map("file")
    glob = store.agg_map("global").get("global")
    suite_rate = glob["mean"] if glob else 0.0
    preds: list[TestPred] = []
    sources = {"test": 0, "file": 0, "suite": 0, "unmeasured": 0}
    for k in keys:
        a = agg.get(k)
        if a and a["n"] and a["mean"]:
            vals = store.durations(k)
            preds.append(TestPred(k, _location(vals) if vals else a["mean"],
                                  _sigma_log(vals, a["p50"] or a["mean"]), True))
            sources["test"] += 1
            continue
        f = files.get(file_of(k) or "")
        if f and f["mean"]:
            preds.append(TestPred(k, f["mean"], EXTRAPOLATED_SIGMA, False))
            sources["file"] += 1
            continue
        if suite_rate:
            preds.append(TestPred(k, suite_rate, EXTRAPOLATED_SIGMA, False))
            sources["suite"] += 1
            continue
        preds.append(TestPred(k, 0.0, EXTRAPOLATED_SIGMA, False))
        sources["unmeasured"] += 1
    basis = ("measured" if sources["test"] == len(keys) else
             "measured+extrapolated" if sources["unmeasured"] < len(keys) else "cold")
    return preds, {"source_counts": sources, "basis": basis}


def _quantiles(preds: list[TestPred], draws: int = 400, seed: int = 7) -> tuple[float, float, float]:
    """p50/p90/p99 of the suite total.

    Monte-Carlo below 200 tests (discrete effects matter), closed-form lognormal
    moment matching above it (O(n), no draws: a 60k-test suite must not pay
    400 x 60k exponent evaluations inside every plan).
    """
    if len(preds) >= 200:
        mean = 0.0
        var = 0.0
        for p in preds:
            s2 = p.sigma_log ** 2
            m = math.exp(math.log(max(p.median, 1e-9)) + 0.5 * s2)
            mean += m
            var += m * m * (math.exp(s2) - 1.0)
        cv2 = var / (mean * mean) if mean > 0 else 0.0
        sig = math.sqrt(math.log1p(cv2)) if cv2 > 0 else 0.0
        mu = math.log(mean) - 0.5 * sig * sig if mean > 0 else 0.0
        return (math.exp(mu), math.exp(mu + 1.2816 * sig),
                math.exp(mu + 2.3263 * sig))
    rng = random.Random(seed)
    totals = []
    for _ in range(draws):
        tot = 0.0
        for p in preds:
            tot += math.exp(math.log(max(p.median, 1e-9)) + rng.gauss(0.0, p.sigma_log))
        totals.append(tot)
    totals.sort()
    q = lambda f: totals[min(len(totals) - 1, int(f * len(totals)))]
    return q(0.5), q(0.9), q(0.99)


def estimate(keys: list[str], store, safety: float = 1.25) -> Estimate:
    """Suite estimate from this store's measurements."""
    preds, calibration = predict_tests(keys, store)
    p50, p90, p99 = _quantiles(preds)
    # A tail wider than this is handled by resuming the run, not by reserving
    # time the suite will not use.
    p90 = min(p90, p50 * 8.0)
    p99 = min(p99, p50 * 20.0)
    matched = sum(p.median for p in preds if p.matched)
    total = sum(p.median for p in preds) or 1.0
    return Estimate(
        p50=p50, p90=p90, p99=p99, coverage=matched / total, preds=preds,
        unknowns=[p.key for p in preds if not p.matched],
        breakdown={"tests_ms": total, "n_tests": len(preds),
                   "matched_mass_ms": matched, **calibration},
        calibration=calibration,
        budget_s=round(p90 * safety / 1000.0, 3),
    )
