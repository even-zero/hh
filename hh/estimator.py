"""Estimator: pure core (ARCHITECTURE §7). Tiered fuzzy match + MC quantiles."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .keys import file_of

# Bundled static priors (T5), per runner class. Order-of-magnitude constants:
# a "typical" unit test in each ecosystem, in ms.
PRIORS_MS = {"pytest": 80.0, "unittest": 60.0, "jest": 120.0, "node": 80.0,
             "go": 40.0, "generic": 100.0}
PRIOR_SIGMA_LOG = 0.9          # wide: we are guessing
MATCHED_SIGMA_FLOOR = 0.25     # even known tests vary a bit

TIER_W = {0: 1.0, 1: 0.7, 2: 0.6, 3: 0.4, 4: 0.25, 5: 0.1}


@dataclass
class TestPred:
    key: str
    median: float            # blended point estimate (ms)
    sigma_log: float         # log-normal spread for MC draws
    tier: int
    matched: bool            # counted toward coverage (T0–T1)
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
    coverage: float = 0.0
    unknowns: list = field(default_factory=list)
    preds: list = field(default_factory=list)
    overhead_ms: float = 0.0


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


def predict_tests(keys: list[str], store, runner: str) -> list[TestPred]:
    """Tiered fuzzy matching (§7.2). T0 exact · T3 file · T5 bundled prior.

    T1/T2 (content-hash decay, fuzzy name) need source hashes / name indexes;
    they hook in here when the adapter supplies them (M2). Structure is ready:
    any richer matcher just raises the tier before the T3 fallthrough.
    """
    tests = store.agg_map("test")
    files = store.agg_map("file")
    prior = PRIORS_MS.get(runner, PRIORS_MS["generic"])
    preds: list[TestPred] = []
    for key in keys:
        a = tests.get(key)
        if a and a["n"] > 0:
            med = _robust_location([a["p50"], a["mean_ewma"]])
            sig = _sigma_log([a["p50"], a["p90"], a["mean_ewma"]], med)
            preds.append(TestPred(key, med, sig, 0, True, a["n_censored"]))
        else:
            f = files.get(file_of(key))
            if f and f["n"] > 0:
                w = TIER_W[3]
                med = w * f["mean_ewma"] + (1 - w) * prior
                sig = max(_sigma_log([f["p50"], f["p90"]], f["p50"] or prior), 0.5)
                preds.append(TestPred(key, med, sig, 3, False))
            else:
                preds.append(TestPred(key, prior, PRIOR_SIGMA_LOG, 5, False))
    return preds


def estimate(keys: list[str], store, runner: str = "unittest", workers: int = 1,
             c0_ms: float | None = None, safety: float = 1.15,
             cold_safety: float = 2.0, draws: int = 400, seed: int = 7) -> Estimate:
    """Suite estimate with quantiles via Monte-Carlo convolution (§7.3–7.4)."""
    preds = predict_tests(keys, store, runner)
    if c0_ms is None:
        # measured collection/startoverhead from history, else conservative default
        c0_ms = 150.0

    rng = random.Random(seed)
    totals = []
    for _ in range(draws):
        tot = c0_ms * rng.uniform(0.8, 1.2)
        for p in preds:
            tot += math.exp(math.log(p.median) + rng.gauss(0.0, p.sigma_log))
        totals.append(tot)
    totals.sort()
    q = lambda f: totals[min(len(totals) - 1, int(f * len(totals)))]
    p50 = q(0.5)
    # sanity caps on quantile spread: tails beyond this are handled by sharding
    # and per-test cutoffs, not by inflating the budget
    p90 = min(q(0.9), p50 * 8.0)
    p99 = min(q(0.99), p50 * 20.0)

    matched_mass = sum(p.median for p in preds if p.matched)
    total_mass = sum(p.median for p in preds) or 1.0
    coverage = matched_mass / total_mass

    est = Estimate(
        p50=p50, p90=p90, p99=p99, point=p50, coverage=coverage,
        unknowns=[p.key for p in preds if not p.matched],
        preds=preds,
        breakdown={
            "overhead_c0_ms": c0_ms,
            "tests_ms": total_mass,
            "n_tests": len(preds),
            "matched_mass_ms": matched_mass,
        },
    )
    # Budget rule (§7.5): safety scales up on cold/low-coverage estimates.
    s = safety + (cold_safety - safety) * (1.0 - coverage)
    est.recommended_timeout = p90 * s / 1000.0
    return est
