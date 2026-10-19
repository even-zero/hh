# Fresh-repo benchmark vs the existing 2026-10-19 report

**This is not a machine-generated baseline diff.** The baseline in
`benchmark/baseline/` describes the five original repositories; this run uses
five different repositories. The comparison below puts both sets on the same
normalized axes and marks whether a difference is *like-for-like* or merely
descriptive. Raw numbers: `2026-10-19-fresh-repos-results.json`.

Environment: Python 3.11.2, Node 22.22.3, 2 CPUs, Linux. hh 1.0.0
(current HEAD `0d33703`; the existing report's meta records `287970c`).

## Like-for-like axes

| Axis | Existing report (original repos) | Fresh set (new repos) | Read |
|---|---|---|---|
| Discovery, measured universe | packaging 62,493/62,493; itsdangerous 297/297; markupsafe 80/80; chai 512/512; tornado 1,575 native | markdown 1,129/1,129; click 2,287/2,287; express 1,153/1,153*; werkzeug 994 selected / 1,019 probe; urllib3 2,048 selected / 2,808 probe | Parity for flat suites; *express's 108 `test/acceptance/` tests are invisible to hh's non-recursive Mocha adapter. Capture probe counts exceed clean counts because of `-m`/`--ignore` selections. |
| Capture overhead, 2% gate | packaging **+13.0%** @ 62,493 tests, 29.75 s (fails) | urllib3 **+0.3%** @ 2,048 tests, 11.39 s (passes); werkzeug **+3.6%** @ 994 tests, 4.20 s (fails; 7.8% over 3 alternating runs) | One pass, one fail. Not a controlled comparison: suite sizes and per-test durations differ by 5–30×. |
| Executor full cost (`hh plan` + run vs naive) | tornado **−1.6%**; itsdangerous **+58.2%**; markupsafe **+48.1%**; chai **+17.8%** | markdown **+21.7%**; express **+13.9%**; click **+124.6%** | Mixed. Express is slightly better than chai; click is much worse than any old exec repo. |
| Warm wall vs naive | tornado 28.55 s vs 29.36 s (**−2.8%**) | markdown 0.748 vs 0.746 (**+0.3%**); express 2.788 vs 2.787 (**+0.04%**); click 8.21 vs 4.79 (**+71.6%**) | Parity on two suites, regression on click. |
| Warm prediction error (`predicted_wall_s`) | tornado **1.0%** W1 / **5.5%** W2 | markdown **17.5%** / **19.3%**; express **16.3%** / **36.5%**; click **74.9%** / **83.6%** | Worse. Old tiny suites were also 36–80% off (itsdangerous 49.6%, markupsafe 80.3%, chai 36.3%), so this is the known small-suite limitation, not a new capability. |
| Resume | tornado **31.0 s vs 42.9 s** naive (−27.7%), receipt 13.8 s | express **4.12 s vs 4.75 s** (−13.2%), receipt 1.6 s; click **8.73 s vs 8.64 s** (+1.0%), receipt 2.1 s | At best modest; click gets no wall win. |
| Hang | tornado exec: naive 210 s, hh steady state 33.6 s; packaging capture: naive 90 s, hang identified, descope 33.75 s | werkzeug capture: naive 90 s (no result), capture 93.85 s (30 + 60 + 3.84 s descope), hang identified `tests/test_zzhang.py::test_never_finishes` | Mechanism parity. On a 3.8 s suite the capture arm nearly matches naive wall; the win is information, not time. |
| Receipts | tornado resume 13.8 s; hang continuation 93.1 s; total 106.9 s | click clean 2.9 s; click resume 2.1 s; express resume 1.6 s | Smaller absolute values. Click's positive receipt coexists with a +124.6% full cost — receipts are work-not-repaid, not net wall savings. |

## Bottom line

- **No marked, general improvement.** On the axes that matter (discovery,
  steady-state overhead, prediction, resume, hang) the fresh set reproduces the
  existing report's profile: full discovery for flat suites, small-suite
  overhead far above 2%, good resume wins only when the suite is large enough
  to amortize planning, and hang identification that trades wall time for
  knowledge.
- **One material win:** urllib3 capture is **0.3%** in the benchmark run
  (**0.42%** median over three alternating runs), comfortably below the 2%
  gate on a real 2,048-test / 11.4 s pytest suite. This proves the gate is
  achievable; it does not prove a code improvement, because packaging's 13.0%
  was measured on a 62,493-test suite with ~62 µs of ledger cost per test,
  while urllib3's is ~16 µs per test.
- **One material regression:** click's pytest executor doubles warm wall
  (8.21 s vs 4.79 s) and costs **10.75 s plan+run vs 4.79 s naive (+124.6%)**.
  Pytest collection of 33k items (31k stress tests deselected by upstream
  addopts) costs ~2.5 s; the warm shard budget is 3.15 s and does not include
  collection, so the executor kills a nearly complete shard and pays for a
  second collection. This is the strongest argument for budgeting/caching
  collection separately.
- **Discovery is not fully closed:** express's official suite has 108
  `test/acceptance/` tests that hh's Mocha adapter cannot see. They were
  excluded from both arms so the clean comparison stays fair, but the
  capability gap is real.

_Generated for the fresh-repo run on 2026-10-19._
