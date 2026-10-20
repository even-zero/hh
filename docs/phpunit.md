# PHPUnit (`--runner phpunit`)

```bash
hh plan --root . --runner phpunit --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test method**. Resume granularity: one test method.

## What hh needs

- PHPUnit **9.4 or newer** (`--list-tests-xml` appeared there): a project
  `vendor/bin/phpunit`, a `phpunit.phar` in the project, `phpunit` on `PATH`, or
  `HH_PHPUNIT`.
- `--root` at the project directory, so PHPUnit finds its `phpunit.xml`.

## How it works

- **Discovery** runs `phpunit --list-tests-xml <file>` and reads the `testMethod
  id="Tests\Unit\CalcTest::testAdd"` entries. Nothing is executed.
- **Execution** runs `phpunit --teamcity --do-not-cache-result` and reads
  TeamCity service messages: `testStarted` (with a `locationHint` of
  `php_qn://<file>::\<Class>::<method>`, which is the test id), `testFailed`,
  `testIgnored` and `testFinished` (with the duration in milliseconds). Rows are
  written as tests finish, so a kill keeps them.
- A resume passes `--filter '/^(?:id1|id2)(?![A-Za-z0-9_])/'`: PHPUnit matches
  that pattern against the test name and runs only those methods.

## Identity

`phpunit::Tests/Unit/CalcTest/testAdd` — the fully-qualified class and method,
with the class kept as a path segment (the class is the "file" for the store's
file tier).

## Data providers

One method can run many data sets, and PHPUnit reports each one separately. hh
records the **method** once: the status of the worst data set and the sum of
their durations. The row is flushed when the next method starts (or when the run
ends), because only then is the method known to be complete. A kill in the
middle of a data-provider method loses that one method's row and re-runs it —
never the methods before it.

## Gotchas

- **`--do-not-cache-result`** keeps PHPUnit's result cache out of the picture:
  ordering by previous defects/duration would make the same suite take a
  different path on every run, and hh is measuring time.
- **PHPUnit 8 and older** have no `--list-tests-xml`; discovery fails with that
  message rather than pretending the suite is empty.
- **`--teamcity` replaces the normal progress output**, so a project that
  customises its printer sees TeamCity messages instead during an hh run. The
  project's printer is unaffected on a normal `phpunit` invocation.
- **A test that errors in `setUp`** still produces `testStarted`/`testFailed`;
  it is recorded as `failed`, like any other error.
