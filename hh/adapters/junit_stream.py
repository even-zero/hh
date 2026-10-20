"""Run a JUnit Platform suite through hh: discover, execute, stream a ledger.

Works for anything that speaks the JUnit Platform — JUnit 5 (Jupiter), JUnit 4
through the Vintage engine, and Kotlin test suites compiled against either.

What hh needs from a runner is small and JUnit does not offer it out of the
box: a per-test ledger written *while* the suite runs (a SIGKILL must keep the
finished rows) and the ability to execute an explicit set of test ids. Both
live in ``HhJunit.java``, a ~300-line driver this module compiles once with
``javac`` and then runs with ``java``.

Requirements, checked here with actionable errors:

  * a JDK (``javac``), not just a JRE — the driver is compiled on first use;
  * the project's *test* classpath, which must contain the JUnit Platform
    launcher. hh finds it in this order:
      1. ``HH_JUNIT_CLASSPATH`` (path-separator separated, used verbatim);
      2. ``mvn dependency:build-classpath`` when ``pom.xml`` and ``mvn`` exist;
      3. a ``junit-platform-console-standalone-*.jar`` in the project.
  * compiled test classes (``mvn test-compile`` / ``gradle testClasses``), in
    the usual output directories (``target/test-classes``,
    ``build/classes/java/test``, ``build/classes/kotlin/test``, ...).

Gradle projects usually need ``HH_JUNIT_CLASSPATH``; see README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER_SRC = os.path.join(HERE, "HhJunit.java")

# Where the usual build tools put compiled test / main classes.
TEST_CLASS_DIRS = (
    "target/test-classes",
    "build/classes/java/test",
    "build/classes/kotlin/test",
    "out/test/classes",
    "build/test/classes",
)
MAIN_CLASS_DIRS = (
    "target/classes",
    "build/classes/java/main",
    "build/classes/kotlin/main",
    "out/classes",
    "build/classes",
)


def _tool(name: str, env_var: str) -> str | None:
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = name + (".exe" if os.name == "nt" else "")
        candidate = os.path.join(home, "bin", exe)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)


def java() -> str | None:
    return _tool("java", "HH_JAVA")


def javac() -> str | None:
    return _tool("javac", "HH_JAVAC")


def test_class_dirs(root: str) -> list[str]:
    return [os.path.join(root, d) for d in TEST_CLASS_DIRS
            if os.path.isdir(os.path.join(root, d))]


def _standalone_jars(root: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = os.path.relpath(dirpath, root).count(os.sep)
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", ".hh", "build", "target")]
        if depth >= 2:
            dirnames[:] = []
        for f in filenames:
            if f.startswith("junit-platform-console-standalone") and f.endswith(".jar"):
                found.append(os.path.join(dirpath, f))
    return found


def _maven_classpath(root: str, cache: str) -> list[str]:
    pom = os.path.join(root, "pom.xml")
    if not os.path.exists(pom):
        return []
    mvn = os.environ.get("HH_MVN") or shutil.which("mvn")
    if not mvn:
        return []
    out = os.path.join(cache, "maven-test-classpath.txt")
    if not (os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(pom)):
        proc = subprocess.run(
            [mvn, "-q", "-DincludeScope=test", "dependency:build-classpath",
             f"-Dmdep.outputFile={out}"],
            cwd=root, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(
                "could not resolve the Maven test classpath; set "
                "HH_JUNIT_CLASSPATH to `mvn dependency:build-classpath` output.\n"
                + (proc.stdout + proc.stderr)[-1500:])
    with open(out, encoding="utf-8") as f:
        return [p for p in f.read().strip().split(os.pathsep) if p]


def classpath(root: str, cache: str) -> list[str]:
    """The project's test runtime classpath (dependencies + main classes)."""
    parts = [os.path.join(root, d) for d in MAIN_CLASS_DIRS
             if os.path.isdir(os.path.join(root, d))]
    explicit = os.environ.get("HH_JUNIT_CLASSPATH")
    if explicit:
        parts += [p for p in explicit.split(os.pathsep) if p]
        return parts
    parts += _maven_classpath(root, cache)
    parts += _standalone_jars(root)
    return parts


def has_platform(entries: list[str]) -> bool:
    return any("junit" in os.path.basename(p).lower() for p in entries if p)


def cache_dir(root: str) -> str:
    path = os.path.join(root, ".hh", "junit")
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".w")
        with open(probe, "w", encoding="utf-8"):
            pass
        os.remove(probe)
        return path
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(),
                                "hh-junit-" + hashlib.sha1(root.encode()).hexdigest()[:8])
        os.makedirs(fallback, exist_ok=True)
        return fallback


def driver_classes(cache: str, cp: list[str]) -> str:
    """Compile HhJunit.java once per source revision."""
    with open(DRIVER_SRC, "rb") as f:
        tag = hashlib.sha1(f.read()).hexdigest()[:12]
    out = os.path.join(cache, "driver-" + tag)
    if os.path.exists(os.path.join(out, "HhJunit.class")):
        return out
    jc = javac()
    if not jc:
        raise RuntimeError(
            "hh's JUnit adapter compiles a small listener and needs a JDK "
            "(javac). Install a JDK, or set HH_JAVAC to one.")
    os.makedirs(out, exist_ok=True)
    proc = subprocess.run([jc, "-nowarn", "-cp", os.pathsep.join(cp), "-d", out,
                           DRIVER_SRC], capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(os.path.join(out, "HhJunit.class")):
        raise RuntimeError("javac failed:\n" + (proc.stdout + proc.stderr)[-2000:])
    return out


def _environment(root: str, cache: str) -> tuple[str, str, str]:
    if not java():
        raise RuntimeError("hh's JUnit adapter needs a JVM (java on PATH, "
                           "JAVA_HOME, or HH_JAVA).")
    if not test_class_dirs(root):
        raise RuntimeError(
            "no compiled test classes found under " + root + " (looked for "
            + ", ".join(TEST_CLASS_DIRS) + "). Build the tests first: "
            "`mvn test-compile` or `gradle testClasses`.")
    cp = classpath(root, cache)
    if not has_platform(cp):
        raise RuntimeError(
            "the test classpath does not contain the JUnit Platform. Set "
            "HH_JUNIT_CLASSPATH (for Gradle: the test runtime classpath), or "
            "put junit-platform-console-standalone-*.jar in the project.")
    classes = driver_classes(cache, cp)
    return java(), os.pathsep.join([classes] + cp), os.pathsep.join(test_class_dirs(root))


def discover(root: str, out_path: str) -> int:
    jvm, cp, scan = _environment(root, cache_dir(root))
    proc = subprocess.run([jvm, "-cp", cp, "HhJunit", "discover",
                           "--scan", scan, "--out", out_path],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("JUnit discovery failed:\n" + (proc.stdout + proc.stderr)[-2000:])
    # The driver and hh/keys.py must agree on every id, or the next run would
    # look for tests the ledger never writes. Fail here, where it is readable.
    from ..keys import unique_key_map
    with open(out_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    expected = unique_key_map([r["raw_id"] for r in rows], "junit")
    wrong = [r for r in rows if r.get("key") != expected.get(r["raw_id"])]
    if wrong:
        raise RuntimeError(
            "HhJunit.java and hh/keys.py disagree on test identity "
            f"({len(wrong)} ids, e.g. {wrong[0]['raw_id']!r}: driver "
            f"{wrong[0].get('key')!r} vs {expected.get(wrong[0]['raw_id'])!r})")
    return 0


def run(root: str, ledger: str, tests_file: str | None) -> int:
    jvm, cp, scan = _environment(root, cache_dir(root))
    cmd = [jvm, "-cp", cp, "HhJunit", "run", "--scan", scan, "--ledger", ledger]
    if tests_file:
        cmd += ["--ids", tests_file]
    proc = subprocess.run(cmd, text=True)
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hh.adapters.junit_stream")
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger")
    ap.add_argument("--tests-file")
    ap.add_argument("--collect-out")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    try:
        if args.collect_out:
            return discover(root, args.collect_out)
        if not args.ledger:
            ap.error("--ledger is required unless --collect-out is given")
        return run(root, args.ledger, args.tests_file)
    except RuntimeError as e:
        print(f"hh: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
