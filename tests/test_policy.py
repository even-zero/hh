"""Planner rules that the whole cost story rests on.

  * predicted makespan (LPT simulation) — E7 holds it to ≤2.8 % of measured wall
  * choose_workers(): smallest W that fits a CI deadline; honest infeasibility
  * imbalance stop: stop adding workers when the marginal gain < 5 %
  * auto-degrade: tiny suites get the record-only path (E4)
"""
import unittest

from hh import policy
from hh.estimator import Estimate, TestPred


def make_est(medians_ms: list[float]) -> Estimate:
    preds = [TestPred(key=f"k/others/t{i:03d}", median=m, sigma_log=0.25, tier=0,
                      matched=True) for i, m in enumerate(medians_ms)]
    total = sum(medians_ms)
    return Estimate(p50=total, p90=total * 1.15, p99=total * 1.3, point=total,
                    preds=preds, coverage=1.0)


LIVE_LIKE = [3000.0] * 8 + [24.0] * 100          # 8 slow tests ≈ the live fixtures
POLE_LIKE = [3000.0, 3000.0] + [70.0] * 39       # synth_a_41: one unsplittable pole


class TestMakespan(unittest.TestCase):
    def test_lpt_balancing(self):
        four = [1000.0] * 4
        self.assertEqual(policy.makespan_ms([TestPred("k/n/a", m, .25, 0, True)
                                             for m in four], 1), 4000.0)
        preds = [TestPred(f"k/n/t{i}", m, .25, 0, True) for i, m in enumerate(four)]
        self.assertEqual(policy.makespan_ms(preds, 2), 2000.0)
        self.assertEqual(policy.makespan_ms(preds, 4), 1000.0)

    def test_more_workers_never_worse(self):
        est = make_est(LIVE_LIKE)
        spans = [policy.makespan_ms(est.preds, w) for w in (1, 2, 3, 4)]
        self.assertEqual(spans, sorted(spans, reverse=True))


class TestChooseWorkers(unittest.TestCase):
    def test_smallest_w_that_fits_deadline(self):
        est = make_est(LIVE_LIKE)
        spans = {w: policy.makespan_ms(est.preds, w) / 1000 for w in (1, 2, 3, 4)}
        self.assertAlmostEqual(spans[1], 26.4, places=1)
        self.assertAlmostEqual(spans[2], 13.2, places=1)
        self.assertAlmostEqual(spans[3], 9.0, places=1)
        self.assertAlmostEqual(spans[4], 6.6, places=1)
        # smallest W whose predicted wall fits each deadline
        self.assertEqual(policy.choose_workers(est, deadline_s=40, max_workers=4)["workers"], 1)
        self.assertEqual(policy.choose_workers(est, deadline_s=15, max_workers=4)["workers"], 2)
        self.assertEqual(policy.choose_workers(est, deadline_s=12, max_workers=4)["workers"], 3)
        # 9.0 s does not fit 8.5 s, so the 4th worker is required
        self.assertEqual(policy.choose_workers(est, deadline_s=8.5, max_workers=4)["workers"], 4)

    def test_infeasible_deadline_is_reported_not_faked(self):
        est = make_est(LIVE_LIKE)
        d = policy.choose_workers(est, deadline_s=5, max_workers=4)
        self.assertFalse(d["feasible"])
        self.assertEqual(d["workers"], 4)               # best effort, honestly flagged
        self.assertGreater(d["predicted_wall_s"], 1.0)
        self.assertIn("INFEASIBLE", d["rationale"])

    def test_imbalance_stop(self):
        """The 3 s pole cannot split: W=4 must show ~0 % marginal gain and stop."""
        est = make_est(POLE_LIKE)
        d = policy.choose_workers(est, deadline_s=None, max_workers=4)
        self.assertEqual(d["workers"], 3)
        self.assertLess(d["curve"][3]["gain_pct"], 5.0)
        self.assertGreater(d["curve"][1]["gain_pct"], 30.0)   # W=2 was a real gain

    def test_cap_defaults_to_cpu_count(self):
        est = make_est(LIVE_LIKE)
        d = policy.choose_workers(est, deadline_s=999)
        import os
        self.assertLessEqual(d["max_workers"], os.cpu_count() or 1)


class TestMakePlan(unittest.TestCase):
    def test_predicted_wall_present_and_positive(self):
        est = make_est(LIVE_LIKE)
        raw_of = {p.key: p.key for p in est.preds}
        plan = policy.make_plan(est, raw_of, workers=2, checkpoint_dir="/tmp/x")
        self.assertGreater(plan.predicted_wall_s, 0)
        self.assertEqual(plan.workers_hint, 2)
        self.assertIn("predicted_wall_s", plan.to_json())

    def test_tiny_suite_degrades_to_serial_no_sharding(self):
        est = make_est([20.0] * 12)                     # point = 240 ms < 1000 ms
        raw_of = {p.key: p.key for p in est.preds}
        plan = policy.make_plan(est, raw_of, workers=2, checkpoint_dir="/tmp/x")
        self.assertEqual(plan.operating_level, "tiny")
        self.assertEqual(len(plan.shards), 1)
        self.assertEqual(plan.workers_hint, 1)

    def test_deadline_decision_recorded_in_plan(self):
        est = make_est(LIVE_LIKE)
        raw_of = {p.key: p.key for p in est.preds}
        plan = policy.make_plan(est, raw_of, workers=2, checkpoint_dir="/tmp/x",
                                deadline_s=9, max_workers=4)
        self.assertIsNotNone(plan.deadline_decision)
        self.assertEqual(plan.workers_hint, plan.deadline_decision["workers"])
        self.assertEqual(len(plan.shards), plan.deadline_decision["workers"])


if __name__ == "__main__":
    unittest.main()
