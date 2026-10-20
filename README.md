# hh — the timeout your test suite actually needs

A caller that runs a test suite has to give it a timeout. If the timeout is too
low the suite is **SIGKILLed**, the work is thrown away, and the caller runs it
again from zero. hh replaces that guess with a measurement: it records how long
each test takes, and from the second run on it hands out a budget derived from
those measurements.

It does one thing. There is no sampling, no prior, no parallelism, no scoring.

```
first call    run the suite like the naive command, and record it   (no prediction)
later calls   budget = p90(measured suite) x 1.25                   (a measurement)
on overrun    resume from the ledger; finished tests are never re-paid
```

Stdlib-only core · zero install · no network · SQLite store inside the repo.

## Quickstart

As a tool (the agent-facing surface):

```python
from hh.tool import handle_tool_call

result = handle_tool_call("run_tests", {"root": "/path/to/repo", "runner": "pytest"})
# -> {"status": "ok",
#     "operating_level": "measured",
#     "budget": {"basis": "measured", "budget_s": 24.7, "p50_s": 17.5,
#                "p90_s": 19.8, "coverage": 1.0, "confidence": 1.0},
#     "wall_s": 17.9, "attempts": 1, "timed_out": 0, ...}
```

As a CLI:

```bash
hh plan --root . --runner pytest --out plan.json   # measure, emit the budget
hh run  --plan plan.json --root .                  # execute + record
hh report                                          # what the store has measured
```

`--runner` is one of `unittest`, `pytest`, `node` (Mocha) or `junit` (any test
framework on the JUnit Platform — JUnit 5, JUnit 4 through Vintage, and Kotlin
suites compiled against either). The store lives in `./.hh/` inside the suite
root (gitignore it, or point `--store-root` elsewhere).

## Java and Kotlin (JUnit)

hh drives the JUnit Platform directly, so Java and Kotlin suites get the same
treatment as the Python ones: a record pass, then a measured budget, and a
resume that never re-pays a finished test.

Two things are needed, because JUnit gives neither for free:

- **a JDK**, not just a JRE — hh compiles a small driver
  (`hh/adapters/HhJunit.java`) on first use and caches it in `.hh/junit/`;
- **the project's test classpath** and **compiled test classes**. Build first
  (`mvn test-compile`, `gradle testClasses`); hh looks in
  `target/test-classes`, `build/classes/java/test`, `build/classes/kotlin/test`
  and the usual siblings.

The classpath is resolved in this order, and the error tells you which step
failed:

1. `HH_JUNIT_CLASSPATH` — used verbatim (path-separator separated), for Gradle
   projects or any build tool hh does not know. Point it at the test runtime
   classpath including the JUnit jars, e.g. from your IDE, or add a one-line
   task that prints `sourceSets.test.runtimeClasspath.asPath`.
2. `mvn dependency:build-classpath` when `pom.xml` and `mvn` are present
   (cached next to the compiled driver).
3. a `junit-platform-console-standalone-*.jar` somewhere in the project.

```bash
hh --store-root . plan --root . --runner junit --out plan.json
hh --store-root . run  --plan plan.json --root .          # records the suite
hh --store-root . plan --root . --runner junit --out plan.json   # now measured
```

Identity: a Java test is `junit::com/example/FooTest/testAdd`, with invocations
of a `@ParameterizedTest` tagged by an 8-hex parameter suffix. Unlike the Python
runners, the class segment is kept — in Java the class *is* the file, so
dropping it would merge every same-named method in a package.

Limitation, stated rather than hidden: tests generated at runtime
(`@TestFactory` dynamic tests) are recorded on the run that produces them, but
a later resumed run cannot address them one by one; hh reports them as
`unresolved` and keeps their previous measurements.

## What each call returns

| field | meaning |
|---|---|
| `status` | `ok`, `incomplete` (work left), `shard_failed` (runner died) |
| `budget.basis` | `record` (no measurements yet) or `measured` |
| `budget.budget_s` | the timeout to give this suite next time |
| `budget.p50_s`, `budget.p90_s` | the measured distribution the budget comes from |
| `budget.confidence` | share of the suite that has its own measurements |
| `attempts` / `timed_out` | runner processes started / killed at the budget |
| `wall_s` | what the caller waited |
| `resumed_tests` | tests recognised as already paid for |
| `unresolved` | ids from an old manifest the runner no longer finds (re-record them; not counted as failures) |

## How the budget is computed

1. The suite is enumerated (pytest `--collect-only`, unittest import, Mocha load,
   JUnit Platform discovery), never executed, and the manifest is cached by tree
   fingerprint.
2. Every test gets a point estimate: its own measured rows if it has any
   (median/EWMA blend), else its file's mean, else the suite mean.
3. The suite total is a p50/p90/p99 over those distributions — Monte-Carlo below
   200 tests, closed-form lognormal moment matching above.
4. `budget = p90 x 1.25`, floored at 3 s and capped at `4 x p50 + 30 s`.

A cold store has no rows, so step 2 has nothing to work with: the tool says so
(`basis: "record"`, `predicted_wall_s: null`) and records instead of guessing.

## Guarantees, and their honest extent

- **No guess is ever presented as an estimate.** A record pass reports
  `operating_level: "record"` and a null prediction.
- **A wrong budget costs one budget, not the suite.** Finished tests are in the
  ledger; an overrun resumes from the last unfinished test.
- **Only tests that ran count.** Skipped tests and ids that no longer resolve
  are recorded but kept out of the duration aggregates: a 0 ms row would drag
  the p90 down until the budget killed the suite.
- **hh cannot beat a correct guess** on test work. If the suite already fits
  your timeout, hh costs its ledger and nothing else. (It can still be faster
  overall when the naive command goes through a wrapper such as `npm test`:
  hh drives the runner directly. That is a launcher difference, not a timeout
  one.) It is insurance against a too-low timeout, not a speed-up.
- **Small suites are dominated by fixed costs.** Below ~1 s of test time the
  tool's own process start and SQLite writes are a visible percentage.

## Repository layout

```
hh/
  tool.py        the run_tests tool: plan -> execute -> record -> answer
  policy.py      one shard, one budget (p90 x safety)
  estimator.py   measured durations -> p50/p90/p99
  executor.py    run under the budget; resume on overrun
  store.py       SQLite: runs, observations, aggregates
  keys.py        stable test identities
  adapters/      unittest, pytest, Mocha, JUnit (ledger + discovery);
                 HhJunit.java is the JUnit platform driver
benchmark/       ten repos, one metric (see benchmark/README.md)
tests/           unit + end-to-end tests
```

## Development

```bash
python3 -m pip install pytest        # the pytest adapter tests need it
python3 -m unittest discover -s tests -q
python3 -m benchmark setup && python3 -m benchmark run     # the benchmark
```

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.10 and 3.13, smokes
the CLI on `fixtures/synth_tiny_7`, installs the built wheel into a clean venv,
and runs the JUnit adapter against a real JUnit 5 suite with a JDK. The
benchmark is not in CI: it clones ten repositories and takes minutes.

## License

MIT — see `LICENSE`.
