"""The benchmark: one metric, ten repositories, mixed runners.

    python3 -m benchmark setup     # clone + install the repos in repos.json
    python3 -m benchmark run       # measure both arms, write _work/results/*.json
    python3 -m benchmark report    # write REPORT-<date>-timeout-metric.md

Everything generated lives under benchmark/_work/ (gitignored). Numbers are
never synthesized: they come from the two arms actually running the suites.
"""
