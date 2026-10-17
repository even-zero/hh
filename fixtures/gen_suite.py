"""Deterministic synthetic suite generator (EXPERIMENTS.md §2).

Test bodies are time.sleep-based → fully deterministic, zero deps, ground truth
known exactly. Families:
  synth_a     150 fast + 2 slow            — estimation accuracy, E1/E2
  synth_hang  synth_a + 3 infinite-ish     — censoring/quarantine, E3
  synth_tiny  12 fast (<0.2s total)        — auto-degrade, E4
  dual        py + js (node:test)          — cross-runner, E5
Usage: python3 fixtures/gen_suite.py --family synth_a --name synth_a_41 [--grow 20]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random

FAMILIES = {
    "synth_a": dict(n_fast=150, n_slow=2, n_hang=0, n_files=15),
    "synth_hang": dict(n_fast=150, n_slow=2, n_hang=3, n_files=15),
    "synth_tiny": dict(n_fast=12, n_slow=0, n_hang=0, n_files=2),
    "dual": dict(n_fast=20, n_slow=0, n_hang=0, n_files=2),
    # L1 live A/B: slow enough that a plausible naive guess misses, no hangs,
    # so BOTH arms complete and wall/waste compare directly
    "live_slow": dict(n_fast=100, n_slow=8, n_hang=0, n_files=10),
}

PY_TMPL = '''import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class {cls}(unittest.TestCase):
{methods}
'''

METHOD = ('    def test_{t}(self):\n'
          '        time.sleep(DUR["{mod}.{t}"])\n')


def gen_py(out_dir: str, seed: int, fam: dict, grow: int = 0, name: str = "suite") -> dict:
    import hashlib
    tag = hashlib.sha1(name.encode()).hexdigest()[:2]  # unique module tag per suite:
    rng = random.Random(seed)                          # test keys must not collide
    os.makedirs(out_dir, exist_ok=True)                # across synthetic suites
    dur_path = os.path.join(out_dir, "durations.json")
    durations = {}
    if grow and os.path.exists(dur_path):
        durations = json.load(open(dur_path))
    for fn in os.listdir(out_dir):
        if fn.startswith("test_") and fn.endswith(".py"):
            os.remove(os.path.join(out_dir, fn))
    n_files = fam["n_files"] + (grow + 4) // 5
    existing = len([k for k in durations])
    plan = []
    n_fast, n_slow, n_hang = fam["n_fast"], fam["n_slow"], fam["n_hang"]
    total_new = n_fast + n_slow + n_hang + grow
    slow_left, hang_left = n_slow, n_hang
    kinds = {}
    for i in range(total_new):
        if hang_left:
            d, kind, hang_left = 600.0, "hang", hang_left - 1  # true hang: never
        elif slow_left:
            d, kind, slow_left = 3.0 + rng.uniform(-0.3, 0.3), "slow", slow_left - 1
        else:
            d, kind = min(max(rng.lognormvariate(math.log(0.015), 0.7), 0.002), 0.25), "fast"
        plan.append((d, kind))
    for fi in range(n_files):
        mod = f"test_{tag}{fi:03d}"
        cls = f"TestS{fi:03d}"
        methods = []
        per = (total_new + n_files - 1) // n_files
        for j, (d, kind) in enumerate(plan[fi * per:(fi + 1) * per]):
            t = f"t{j:03d}"
            durations[f"{mod}.{t}"] = round(d, 4)
            kinds[f"{mod}.{t}"] = kind
            methods.append(METHOD.format(t=t, mod=mod))
        if methods:
            with open(os.path.join(out_dir, f"{mod}.py"), "w") as f:
                f.write(PY_TMPL.format(cls=cls, methods="".join(methods)))
    json.dump(durations, open(dur_path, "w"), indent=1)
    truth = {"durations": durations, "kinds": kinds,
             "total_test_ms": round(sum(durations.values()) * 1000, 1),
             "total_nonhang_ms": round(sum(v for k, v in durations.items()
                                          if kinds[k] != "hang") * 1000, 1),
             "n": len(durations), "seed": seed}
    json.dump(truth, open(os.path.join(out_dir, ".truth.json"), "w"))
    return truth


JS_TMPL = '''import test from 'node:test';
const DUR = {durations};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
for (const [name, ms] of Object.entries(DUR)) {{
  test(name, async () => {{ await sleep(ms * 1000); }});
}}
'''


def gen_js(out_dir: str, seed: int, n: int = 20) -> dict:
    rng = random.Random(seed + 999)
    os.makedirs(out_dir, exist_ok=True)
    durations = {f"js_t{i:03d}": round(min(max(
        rng.lognormvariate(math.log(0.015), 0.7), 0.002), 0.25), 4) for i in range(n)}
    with open(os.path.join(out_dir, "test_basic.mjs"), "w") as f:
        f.write(JS_TMPL.format(durations=json.dumps(durations)))
    truth = {"durations_js": durations,
             "total_test_ms": round(sum(durations.values()) * 1000, 1), "seed": seed}
    json.dump(truth, open(os.path.join(out_dir, ".truth_js.json"), "w"))
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True, choices=sorted(FAMILIES))
    ap.add_argument("--name", required=True)
    ap.add_argument("--seed", type=int, default=41)
    ap.add_argument("--grow", type=int, default=0)
    ap.add_argument("--out-root", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()
    fam = FAMILIES[args.family]
    out = os.path.join(args.out_root, args.name)
    truth = gen_py(out, args.seed, fam, args.grow, name=args.name)
    print(json.dumps({"py": truth, "out": out}))
    if args.family == "dual":
        js = gen_js(out, args.seed)
        print(json.dumps({"js": js}))


if __name__ == "__main__":
    main()
