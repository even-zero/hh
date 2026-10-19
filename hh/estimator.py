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
RUNNER_OVERHEAD_MS = {"pytest": 1.0, "unittest": 0.1, "node": 0.0}

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
    confidence: float = 1.0  # how much of this prediction is measured (0–1)
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
    # fraction of predicted mass backed by a *measured* duration, either the
    # test's own rows (T0) or a file/global rate extrapolated from sampled rows.
    # `coverage` stays the strict T0–T1 match rate; this one drives the
    # cold-safety multiplier so one sampled run tightens the budget.
    coverage_measured: float = 0.0
    calibration: dict = field(default_factory=dict)
    # Per-test runner bookkeeping the durations do not contain (pytest's
    # setup/call/teardown plumbing): measured at ~1.0 ms/test on click. A
    # budget built from test durations alone under-reserves by that much.
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
                  file_totals: dict[str, int] | None = None,
                  ) -> tuple[list[TestPred], dict]:
    """Tiered fuzzy matching (§7.2) with measured extrapolation.

    T0 exact · T3 file rate · T3g global measured rate · T5 bundled prior.

    Extrapolation is the cold-start answer: a *sample* of rows is measured
    first, then every unmeasured test inherits its file's measured rate. The
    weight on a file's own rows grows with the share of that file actually
    sampled, so the estimate improves monotonically as sampling proceeds and a
    single sampled row cannot speak for a thousand unsampled tests. Files with
    no sample at all fall back to the store-wide measured rate, then to the
    bundled prior (which keeps a truly cold run conservative).

    ``confidence`` records how much of each prediction is backed by
    measurement; `estimate()` turns it into the cold-safety multiplier.

    T1/T2 (content-hash decay, fuzzy name) need source hashes / name indexes;
    they hook in here when the adapter supplies them (M2).
    """
    tests = store.agg_map("test")
    files = store.agg_map("file")
    prior = PRIORS_MS.get(runner, PRIORS_MS["generic"])
    glob = (store.agg_map("global").get("global") or {})
    # Suite-level rate. Use the arithmetic mean of measured rows (the quantity
    # a budget actually needs) once there are enough of them; medians
    # under-predict heavy-tailed suites by 30-50%, and the store's recency EWMA
    # is dominated by whatever ran last. Fewer than 16 rows ⇒ blend with prior.
    g_n = glob.get("n") or 0
    g_mean = glob.get("mean")
    # `is not None`, not truthiness: a genuinely sub-millisecond suite has a
    # measured rate of ~0 and must not be replaced by the 60 ms prior.
    g_rate = float(g_mean) if g_mean is not None else 0.0
    if g_n and g_n < 16:
        g_rate = 0.75 * g_rate + 0.25 * prior
    file_totals = file_totals or {}
    # distinct measured test keys per file (rows repeat across runs; a re-plan
    # must not think a file is fully covered because it re-ran its own sample)
    tests_seen: dict[str, set[str]] = {}
    for tkey in tests:
        tests_seen.setdefault(file_of(tkey), set()).add(tkey)
    preds: list[TestPred] = []
    sources = {"test": 0, "file": 0, "global": 0, "prior": 0}
    for key in keys:
        a = tests.get(key)
        if a and a["n"] > 0:
            med = _robust_location([a["p50"], a["mean_ewma"]])
            sig = _sigma_log([a["p50"], a["p90"], a["mean_ewma"]], med)
            preds.append(TestPred(key, med, sig, 0, True, a["n_censored"], 1.0))
            sources["test"] += 1
            continue
        fkey = file_of(key)
        f = files.get(fkey)
        if f and f["n"] > 0:
            total_in_file = max(file_totals.get(fkey, f["n"]), 1)
            n_distinct = len(tests_seen.get(fkey, ()))
            fcov = min(1.0, n_distinct / total_in_file)
            # Shrinkage: trust a file's own rate only in proportion to how much
            # of the file was sampled (sqrt keeps small-but-real coverage
            # useful). With 1 of 1,378 tests sampled the global rate speaks.
            w = min(0.9, fcov ** 0.5)
            fallback = g_rate if g_n else prior
            file_rate = f.get("mean") if f.get("mean") is not None else f["p50"]
            file_rate = file_rate if file_rate is not None else fallback
            med = w * file_rate + (1 - w) * fallback
            base = f["p50"] or file_rate or fallback or 1e-3
            sig = max(_sigma_log([f["p50"], f["p90"]], base), 0.45 - 0.15 * fcov)
            preds.append(TestPred(key, med, sig, 3, False, 0, round(fcov, 4)))
            sources["file"] += 1
            continue
        if g_n:
            # measured rows exist elsewhere in the suite
            preds.append(TestPred(key, g_rate, 0.4, 3, False, 0, 0.4))
            sources["global"] += 1
            continue
        preds.append(TestPred(key, prior, PRIOR_SIGMA_LOG, 5, False, 0, 0.0))
        sources["prior"] += 1
    calibration = {
        "source_counts": sources,
        "global_rate_ms": round(g_rate, 3) if g_n else None,
        "global_rows": g_n,
        "prior_ms": prior,
        "basis": ("measured" if sources["test"] == len(keys) else
                  "file-extrapolated" if sources["file"] else
                  "global-extrapolated" if sources["global"] else "prior"),
    }
    return preds, calibration


def estimate(keys: list[str], store, runner: str = "unittest", workers: int = 1,
             c0_ms: float | None = None, safety: float = 1.15,
             cold_safety: float = 2.0, draws: int = 400, seed: int = 7) -> Estimate:
    """Suite estimate with quantiles via Monte-Carlo convolution (§7.3–7.4)."""
    file_totals: dict[str, int] = {}
    for k in keys:
        fk = file_of(k)
        file_totals[fk] = file_totals.get(fk, 0) + 1
    preds, calibration = predict_tests(keys, store, runner, file_totals)
    if c0_ms is None:
        # measured collection/startup overhead from history, else conservative default
        c0_ms = 150.0

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
                tot += math.exp(math.log(p.median) + rng.gauss(0.0, p.sigma_log))
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
    # confidence-weighted mass: a file rate estimated from 1 of 500 tests
    # counts for very little, so the cold-safety multiplier stays wide
    measured_mass = sum(p.median * p.confidence for p in preds)
    coverage_measured = measured_mass / total_mass
    calibration["n_sampled_tests"] = calibration["source_counts"]["test"]
    calibration["n_extrapolated"] = (calibration["source_counts"]["file"]
                                     + calibration["source_counts"]["global"])
    calibration["n_prior"] = calibration["source_counts"]["prior"]

    est = Estimate(
        p50=p50, p90=p90, p99=p99, point=p50, coverage=coverage,
        unknowns=[p.key for p in preds if not p.matched],
        preds=preds,
        coverage_measured=coverage_measured,
        calibration=calibration,
        breakdown={
            "overhead_c0_ms": c0_ms,
            "tests_ms": total_mass,
            "n_tests": len(preds),
            "matched_mass_ms": matched_mass,
            "measured_mass_ms": round(measured_mass, 3),
            "coverage_measured": round(coverage_measured, 4),
            "calibration": calibration,
        },
    )
    # per-test framework bookkeeping, measured not guessed (see RUNNER_OVERHEAD_MS)
    est.runner_overhead_ms = round(len(preds) * RUNNER_OVERHEAD_MS.get(runner, 0.0), 3)
    est.breakdown["runner_overhead_ms"] = est.runner_overhead_ms
    # Budget rule (§7.5): safety scales up on cold/unmeasured estimates.
    s = safety + (cold_safety - safety) * (1.0 - coverage_measured)
    est.recommended_timeout = (p90 + est.runner_overhead_ms) * s / 1000.0
    est.p50 += est.runner_overhead_ms
    est.p90 += est.runner_overhead_ms
    return est
