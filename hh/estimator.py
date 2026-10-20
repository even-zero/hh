"""Estimator: turn *measured* durations into a plan (ARCHITECTURE §7).

Scope rule (deliberate, after the 2026-10-20 review): hh does **not** predict
cold suites. A suite with no measurements is executed exactly like a naive run
(``policy.make_record_plan``) and merely recorded; that first pass is what
creates the measurements. From the second run on, every prediction below is
derived from rows this store has actually observed:

  T0  the test itself            (measured p50 / EWMA / p90 ⇒ quantiles)
  T3  the test's file            (mean of that file's measured rows)
  T3s the whole suite            (mean of all measured rows)
  —   nothing measured at all    (no estimate: caller must record first)

The old bundled "typical test is 80 ms" priors are gone on purpose: they were
the mechanism by which a cold estimate guessed, and a guess is what this tool
exists to remove.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .keys import file_of

# sigma for a test that has never been measured but whose file/suite has rows.
# Wider than a measured test (0.25) because it is an extrapolation, but it is
# an extrapolation *from this repository's own data*, not from priors.
UNMEASURED_SIGMA_LOG = 0.6
MATCHED_SIGMA_FLOOR = 0.25     # even known tests vary a bit


@dataclass
class TestPred:
    key: str
    median: float            # blended point estimate (ms)
    sigma_log: float         # log-normal spread for MC draws
    tier: int
    matched: bool            # counted toward coverage (T0)
    censored_hist: int = 0
    hang_tier: int = 0     # 0 normal · 1 censored-once (isolate, generous) · 2 hang (fail-fast)


@dataclass
class Estimate:
    p50: float
    p90: float
    p99: float
    point: float
    recommended_timeout: float = 0.0
    breakdown: dict = field(default_factory=dict)
    coverage: float = 0.0          # share of predicted mass with its own rows
    unknowns: list = field(default_factory=list)
    preds: list = field(default_factory=list)
    overhead_ms: float = 0.0
    calibration: dict = field(default_factory=dict)
    # Per-test runner bookkeeping the durations do not contain (pytest's
    # setup/call/teardown plumbing). *Measured* by the executor from previous
    # runs (`Store.runner_overhead_ms`), never a hard-coded constant.
    runner_overhead_ms: float = 0.0


def _robust_location(vals: list[float]) -> float:
    if not vals:
        return 0.0
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
    # cap: uncensored/censored mixes and single outliers blow sigma up, and
    # exp(gauss(mu, sigma)) then explodes quantiles into absurdity (E3b finding)
    return min(max(sig, MATCHED_SIGMA_FLOOR), 1.5)


def predict_tests(keys: list[str], store, runner: str,
                  ) -> tuple[list[TestPred], dict]:
    """Predict from measurements; never from bundled priors.

    A test with no rows inherits its file's measured mean, else the suite's
    measured mean. When the store has no rows at all every prediction is zero
    and ``calibration["basis"] == "cold"`` — the caller must record a pass
    before planning (``build_plan`` routes that case to a record plan).
    """
    tests = store.agg_map("test")
    files = store.agg_map("file")
    glob = (store.agg_map("global").get("global") or {})
    g_n = glob.get("n") or 0
    g_mean = glob.get("mean")
    # `is not None`, not truthiness: a genuinely sub-millisecond suite has a
    # measured rate of ~0 and must not be replaced by anything.
    g_rate = float(g_mean) if g_mean is not None else 0.0

    preds: list[TestPred] = []
    sources = {"test": 0, "file": 0, "suite": 0, "unmeasured": 0}
    for key in keys:
        a = tests.get(key)
        if a and a["n"] > 0:
            med = _robust_location([a["p50"], a["mean_ewma"]])
            sig = _sigma_log([a["p50"], a["p90"], a["mean_ewma"]], med)
            preds.append(TestPred(key, med, sig, 0, True, a["n_censored"]))
            sources["test"] += 1
            continue
        f = files.get(file_of(key))
        if f and f["n"] > 0 and f.get("mean") is not None:
            preds.append(TestPred(key, float(f["mean"]), UNMEASURED_SIGMA_LOG, 3, False))
            sources["file"] += 1
            continue
        if g_n and g_rate > 0:
            preds.append(TestPred(key, g_rate, UNMEASURED_SIGMA_LOG, 3, False))
            sources["suite"] += 1
            continue
        preds.append(TestPred(key, 0.0, UNMEASURED_SIGMA_LOG, 4, False))
        sources["unmeasured"] += 1
    basis = ("cold" if sources["unmeasured"] == len(keys) else
             "measured" if sources["test"] == len(keys) else "measured+extrapolated")
    calibration = {
        "source_counts": sources,
        "suite_rate_ms": round(g_rate, 3) if g_n else None,
        "suite_rows": g_n,
        "basis": basis,
    }
    return preds, calibration


def estimate(keys: list[str], store, runner: str = "unittest", workers: int = 1,
             c0_ms: float | None = None, safety: float = 1.15,
             runner_overhead_ms: float = 0.0,
             draws: int = 400, seed: int = 7) -> Estimate:
    """Suite estimate with quantiles via Monte-Carlo convolution (§7.3–7.4).

    All input durations are measured; ``runner_overhead_ms`` is the per-test
    bookkeeping measured on previous runs of this runner (0.0 on a cold store,
    but a cold store never reaches here).
    """
    preds, calibration = predict_tests(keys, store, runner)
    if c0_ms is None:
        # collection/startup is modelled by the plan's measured startup_s; this
        # term stays as the small per-run variance floor used by the MC.
        c0_ms = 0.0

    # Quantiles: Monte-Carlo below MOMENT_N tests (discrete effects matter),
    # lognormal moment-matching above it. The closed form is O(n) with no
    # draws — a 62k-test suite otherwise pays 400 x 62k exponent evaluations
    # inside every plan, which is tool overhead charged for nothing.
    MOMENT_N = 200
    if len(preds) >= MOMENT_N:
        mean = c0_ms
        var = 0.0
        for p in preds:
            s2 = p.sigma_log * p.sigma_log
            m = math.exp(math.log(max(p.median, 1e-9)) + 0.5 * s2)
            mean += m
            var += (m * m) * (math.exp(s2) - 1.0)
        cv2 = var / (mean * mean) if mean > 0 else 0.0
        sig = math.sqrt(math.log1p(cv2)) if cv2 > 0 else 0.0
        mu = math.log(mean) - 0.5 * sig * sig if mean > 0 else 0.0
        p50 = math.exp(mu)
        p90 = math.exp(mu + 1.2816 * sig)
        p99 = math.exp(mu + 2.3263 * sig)
    else:
        rng = random.Random(seed)
        totals = []
        for _ in range(draws):
            tot = c0_ms * rng.uniform(0.8, 1.2)
            for p in preds:
                tot += math.exp(math.log(max(p.median, 1e-9)) + rng.gauss(0.0, p.sigma_log))
            totals.append(tot)
        totals.sort()
        q = lambda f: totals[min(len(totals) - 1, int(f * len(totals)))]
        p50 = q(0.5)
        p90 = q(0.9)
        p99 = q(0.99)
    # sanity caps on quantile spread: tails beyond this are handled by sharding
    # and per-test cutoffs, not by inflating the budget
    p90 = min(p90, p50 * 8.0)
    p99 = min(p99, p50 * 20.0)

    matched_mass = sum(p.median for p in preds if p.matched)
    total_mass = sum(p.median for p in preds) or 1.0
    coverage = matched_mass / total_mass

    est = Estimate(
        p50=p50, p90=p90, p99=p99, point=p50, coverage=coverage,
        unknowns=[p.key for p in preds if not p.matched],
        preds=preds, calibration=calibration,
        breakdown={
            "overhead_c0_ms": c0_ms,
            "tests_ms": total_mass,
            "n_tests": len(preds),
            "matched_mass_ms": matched_mass,
            "calibration": calibration,
        },
    )
    # Per-test framework bookkeeping: measured on previous runs, not guessed.
    est.runner_overhead_ms = round(len(preds) * max(0.0, runner_overhead_ms), 3)
    est.breakdown["runner_overhead_ms"] = est.runner_overhead_ms
    # Budget rule (§7.5). Widen when part of the suite has never been measured
    # (`coverage` < 1), because the extrapolated tests carry UNMEASURED_SIGMA.
    est.recommended_timeout = (p90 + est.runner_overhead_ms) * safety / 1000.0
    est.p50 += est.runner_overhead_ms
    est.p90 += est.runner_overhead_ms
    return est
