"""hh/benchmark — the pinned, config-driven benchmark suite for hh.

    python3 -m benchmark list            # show repos + scenarios
    python3 -m benchmark setup           # clone pinned refs, deps, shims, hang variants
    python3 -m benchmark run [-q]        # execute all scenarios (see list)
    python3 -m benchmark collect         # raw artifacts -> _work/results/results.json
    python3 -m benchmark dashboard       # results.json -> _work/results/dashboard.html
    python3 -m benchmark compare         # current results vs benchmark/baseline/
    python3 -m benchmark all             # setup+run+collect+dashboard

Everything is generated under benchmark/_work/ (gitignored); nothing is written
outside the benchmark directory except OS temp files. Numbers are never
synthesized: metrics come from records.jsonl (one row per executed step), the
hh stores' attempt logs (hh-full rows included), ledgers, and savings receipts.
"""
