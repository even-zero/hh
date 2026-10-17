# Java and Kotlin (JUnit)

```bash
hh --store-root . plan --root . --runner junit --out plan.json
hh --store-root . run  --plan plan.json --root .                  # records the suite
hh --store-root . plan --root . --runner junit --out plan.json    # now measured
```

hh drives the JUnit Platform directly, so Java and Kotlin suites get the same
treatment as the Python ones: a record pass, then a measured budget, and a
resume that never re-pays a finished test.

It covers anything that speaks the JUnit Platform: **JUnit 5 (Jupiter)**,
**JUnit 4 through the Vintage engine**, and **Kotlin** suites compiled against
either.

## What hh needs

Two things, because JUnit gives neither for free:

- **a JDK**, not just a JRE — hh compiles a small driver
  (`executor/adapters/HhJunit.java`) on first use and caches it in
  `.hh/junit/`;
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

The compiled test directories are added to the JVM classpath as well as being
scanned — JUnit's classpath-root selector loads classes through the class
loader, so a directory that is only scanned discovers nothing.

## How it works

- **Discovery** walks the JUnit Platform `TestPlan` and writes one
  `{raw_id, key}` per test. Nothing is executed.
- **Execution** runs the selected ids (or the whole classpath root on a record
  pass) with a listener that appends `start`/`end` rows **as each test
  finishes**, flushing per row. A SIGKILL at the budget therefore keeps every
  finished test, and a resume only runs the remainder.
- A `suite_end` row is written after the listener loop returns, so a killed
  record pass cannot seal a half-finished manifest.

## Identity

A Java test is `junit::com/example/FooTest/testAdd`, with invocations of a
`@ParameterizedTest` tagged by an 8-hex parameter suffix.

Unlike the Python runners, the class segment is kept — in Java the class *is*
the file, so dropping it would merge every same-named method in a package.

The driver and `executor/keys.py` must agree on every id, so discovery
**cross-checks** them: if one id disagrees, discovery fails with that id in the
message rather than letting the next run silently fail to find its tests.

## Limitations, stated rather than hidden

- **Runtime-generated tests.** `@TestFactory` dynamic tests do not exist at plan
  time; they are recorded on the run that produces them, but a later resumed
  run cannot address them one by one. hh reports them as `unresolved` and keeps
  their previous measurements.
- **Surefire-only setups.** If a project can only run through Maven Surefire,
  hh cannot stream per-test rows from a forked JVM; give it the plain Platform
  classpath instead (`HH_JUNIT_CLASSPATH`).
- **Kotlin needs the classes, not the sources.** A Kotlin test source set
  compiled to `build/classes/kotlin/test` is picked up; a `.kts` script with no
  compiled output is not a suite hh can run.
