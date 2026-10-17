"""Live conformance for toolchains beyond Python/JS: Go and the JVM.

Opt-in:  python3 experiments/conformance.py --live-go / --live-jvm / --live-all

Installs what it can into ~/.cache/hh_toolchains (never the repo, never the
workspace snapshot). If a toolchain cannot be obtained, it reports SKIP with
the install hint instead of failing the matrix.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADIR = os.path.join(ROOT, "hh", "adapters")
sys.path.insert(0, ROOT)

CACHE = os.path.expanduser("~/.cache/hh_toolchains")
GO_BIN = os.path.join(CACHE, "go", "bin", "go")
JUNIT_JAR = os.path.join(CACHE, "junit-platform-console-standalone.jar")
# Java 11 in this image → JUnit Platform 1.10.2 (Java 8+). JUnit 6 needs 17+.
JUNIT_VER = "1.10.2"
SCRATCH_GO = "/tmp/hh_go_live"
SCRATCH_JVM = "/tmp/hh_jvm_live"

GO_MOD = "module tdalive\n\ngo 1.21\n"
GO_TEST = '''package calc

import (
\t"os"
\t"testing"
\t"time"
)

func TestFast(t *testing.T) { if 1+1 != 2 { t.Fatal("math") } }

func TestMedium(t *testing.T) { time.Sleep(150 * time.Millisecond) }

func TestFails(t *testing.T) { t.Errorf("intentional failure") }

func TestSkip(t *testing.T) { t.Skip("skipped by design") }

func TestSlow(t *testing.T) {
\tif os.Getenv("HH_SLOW") == "" {
\t\tt.Skip("slow disabled (set HH_SLOW=1)")
\t}
\ttime.Sleep(20 * time.Second)
}
'''

JAVA_TESTS = {
    "SleepyTest.java": '''package hh.live;

import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.Assumptions;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
public class SleepyTest {
    @Test @Order(1) public void fast() { assertTrue(true); }
    @Test @Order(2) public void medium() throws Exception { Thread.sleep(300); }
    @Test @Order(3) public void slow() throws Exception { Thread.sleep(600); }
    @Test @Order(4) public void failing() { assertEquals(1, 2); }
    @Test @Order(5) public void skipped() { Assumptions.assumeTrue(false, "skip me"); }
}
''',
    "MixedTest.java": '''package hh.live;

import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
public class MixedTest {
    @Test @Order(1) public void fastOne() { }
    @Test @Order(2) public void mediumTwo() throws Exception { Thread.sleep(200); }
    @Test @Order(3) public void hangsAtEnd() throws Exception { Thread.sleep(20000); }
}
''',
}

# suffix -> (status, lo_ms, hi_ms)
GO_EXPECT = {
    "TestFast": ("passed", 0, 60),
    "TestMedium": ("passed", 100, 320),
    "TestFails": ("failed", 0, 60),
    "TestSkip": ("skipped", 0, 5),
    "TestSlow": ("skipped", 0, 5),      # slow disabled: env-gated skip
}
JVM_EXPECT = {
    "fast": ("passed", 0, 60),
    "medium": ("passed", 250, 500),
    "slow": ("passed", 550, 900),
    "failing": ("failed", 0, 60),
    "skipped": ("skipped", 0, 60),
}


# ---------------------------------------------------------------- helpers
def _download(url: str, dest: str) -> None:
    tmp = dest + ".part"
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "hh-conformance/1.0"}), timeout=120) as r, \
            open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)


def ensure_go() -> str | None:
    if os.path.exists(GO_BIN):
        return GO_BIN
    print("  SKIP go: not installed. Hint: curl -sL "
          "'https://go.dev/dl/$(curl -s \"https://go.dev/VERSION?m=text\" | head -1)"
          ".linux-amd64.tar.gz' | tar -xz -C ~/.cache/hh_toolchains")
    return None


def ensure_junit() -> str | None:
    if os.path.exists(JUNIT_JAR):
        return JUNIT_JAR
    try:
        url = ("https://repo1.maven.org/maven2/org/junit/platform/"
               f"junit-platform-console-standalone/{JUNIT_VER}/"
               f"junit-platform-console-standalone-{JUNIT_VER}.jar")
        os.makedirs(CACHE, exist_ok=True)
        print(f"  downloading junit-platform-console-standalone {JUNIT_VER} (one-time)...")
        _download(url, JUNIT_JAR)
        return JUNIT_JAR
    except Exception as e:                                  # noqa: BLE001
        print(f"  SKIP jvm: cannot fetch JUnit console jar ({e})")
        return None


def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def _check(rows, expect: dict, key_part: str = "suffix") -> list[str]:
    bad = []
    idx = {r["key"].split("/")[-1]: r for r in rows}
    for name, (status, lo, hi) in expect.items():
        r = idx.get(name)
        if r is None:
            bad.append(f"missing row {name}")
        elif r["status"] != status:
            bad.append(f"{name}: status {r['status']} != {status}")
        elif not (lo <= r["duration_ms"] <= hi):
            bad.append(f"{name}: {r['duration_ms']}ms outside [{lo},{hi}]")
    return bad


# -------------------------------------------------------------------- go
def live_go_matrix() -> int:
    go = ensure_go()
    if go is None:
        return 0                                            # SKIP, not FAIL
    from hh.adapters import go_test, go_stream             # noqa: PLC0415
    from hh.store import Store                             # noqa: PLC0415

    if os.path.isdir(SCRATCH_GO):
        import shutil
        shutil.rmtree(SCRATCH_GO)
    os.makedirs(os.path.join(SCRATCH_GO, "calc"))
    open(os.path.join(SCRATCH_GO, "go.mod"), "w").write(GO_MOD)
    open(os.path.join(SCRATCH_GO, "calc", "calc_test.go"), "w").write(GO_TEST)

    env = dict(os.environ, GOCACHE="/tmp/hh_gocache", GOPATH="/tmp/hh_gopath",
               GOTOOLCHAIN="local", GOFLAGS="")
    fails = 0

    # 1. recorded path: plain `go test -json` → parse()
    p = subprocess.run([go, "test", "-json", "-count=1", "./calc/"],
                       cwd=SCRATCH_GO, env=env, capture_output=True, text=True)
    rows = go_test.parse(p.stdout.encode())
    bad = _check(rows, GO_EXPECT)
    print(f"  live go    parse(): {len(rows)} rows, {len(GO_EXPECT)} expected  "
          f"{'PASS' if not bad else 'FAIL ' + '; '.join(bad)}")
    fails += bool(bad)

    # 2. Tier A path: streaming wrapper → ledger, must agree with parse()
    #    (durations come from different executions — compare identity+status
    #    exactly, durations loosely, and range-check the streamed rows)
    ledger = os.path.join(SCRATCH_GO, "live.jsonl")
    rc = go_stream.stream([go, "test", "-json", "-count=1", "./calc/"], ledger,
                          cwd=SCRATCH_GO, env=env)
    srows = [r for r in _rows(ledger) if r.get("type") == "end"]
    bad_s = _check(srows, GO_EXPECT)
    # rc 1 is the normal exit for a suite with failures — not an error here
    same = (rc in (0, 1) and len(srows) == len(rows)
            and all(a["key"] == b["key"] and a["status"] == b["status"]
                    and abs(a["duration_ms"] - b["duration_ms"]) < 60.0
                    for a, b in zip(sorted(srows, key=lambda r: r["key"]),
                                    sorted(rows, key=lambda r: r["key"])))
            and not bad_s)
    print(f"  live go    stream() vs parse(): {len(srows)} rows  "
          f"{'PASS' if same else 'FAIL ' + '; '.join(bad_s)}")
    fails += (not same)

    # 3. SIGKILL checkpointing: fast tests on disk while TestSlow (20s) runs
    kill_ledger = os.path.join(SCRATCH_GO, "kill.jsonl")
    env_slow = dict(env, HH_SLOW="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "hh.adapters.go_stream", "--ledger", kill_ledger,
         "--cwd", SCRATCH_GO, "--", go, "test", "-json", "-count=1", "./calc/"],
        cwd=ROOT, env=dict(env_slow, PYTHONPATH=ROOT), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    try:
        os.killpg(proc.pid, 9)
    except ProcessLookupError:
        pass
    time.sleep(0.7)
    rows_k = [r for r in _rows(kill_ledger) if r.get("type") == "end"]
    suffs = {r["key"].split("/")[-1]: r["status"] for r in rows_k}
    ok = (suffs.get("TestFast") == "passed" and suffs.get("TestMedium") == "passed"
          and "TestSlow" not in suffs)
    print(f"  kill go    {len(rows_k)} rows checkpointed pre-SIGKILL "
          f"({suffs})  {'PASS' if ok else 'FAIL'}")
    fails += (not ok)

    # 4. ingest into a scratch store: the capture layer feeds the estimator
    store_dir = os.path.join(SCRATCH_GO, "store")
    os.makedirs(store_dir, exist_ok=True)
    st = Store(store_dir)
    run_id = st.start_run("go", suite_sig=None)
    ctx = st.ctx_id(workers=1)
    n = st.ingest_ledger(ledger, run_id, ctx, "go")
    st.refresh_aggregates()
    tracked = len(st.agg_map("test"))
    ok = n == len(rows) and tracked == len(rows)
    print(f"  live go    ingest → store: {n} rows, {tracked} keys tracked  "
          f"{'PASS' if ok else 'FAIL'}")
    fails += (not ok)
    return fails


# ------------------------------------------------------------------- jvm
def _junit_run(jar: str, classes: str, services: str, ledger: str,
               select_class: str, reports: str | None = None,
               timeout: int = 120) -> subprocess.Popen:
    cmd = ["java", "-cp", os.pathsep.join([jar, classes, services]),
           "org.junit.platform.console.ConsoleLauncher", "execute",
           f"--select-class={select_class}", "--details=none",
           "--disable-ansi-colors"]
    if reports:
        cmd.append(f"--reports-dir={reports}")
    env = dict(os.environ, HH_LEDGER=ledger)
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def live_jvm_matrix() -> int:
    jar = ensure_junit()
    if jar is None:
        return 0
    from hh.adapters import junit_xml
    from hh.store import Store

    import shutil
    shutil.rmtree(SCRATCH_JVM, ignore_errors=True)
    classes = os.path.join(SCRATCH_JVM, "classes")
    services = os.path.join(SCRATCH_JVM, "services")
    src = os.path.join(SCRATCH_JVM, "src", "hh", "live")
    os.makedirs(src)
    os.makedirs(os.path.join(services, "META-INF", "services"))
    open(os.path.join(services, "META-INF", "services",
                      "org.junit.platform.launcher.TestExecutionListener"),
         "w").write("hh.HhJUnitListener\n")
    for name, content in JAVA_TESTS.items():
        open(os.path.join(src, name), "w").write(content)
    listener = os.path.join(ADIR, "HhJUnitListener.java")

    cp = subprocess.run(["javac", "-d", classes, "-cp", jar, listener,
                         *[os.path.join(src, f) for f in JAVA_TESTS]],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"  SKIP jvm: javac failed: {cp.stderr.strip()[:300]}")
        return 0
    fails = 0

    # 1. Tier A (listener) + Tier B (Surefire XML) on the same live run
    ledger = os.path.join(SCRATCH_JVM, "live.jsonl")
    reports = os.path.join(SCRATCH_JVM, "reports")
    p = _junit_run(jar, classes, services, ledger, "hh.live.SleepyTest", reports)
    out, _ = p.communicate(timeout=120)
    rows = [r for r in _rows(ledger) if r.get("type") == "end"]
    bad = _check(rows, JVM_EXPECT)
    print(f"  live jvm   listener (Tier A): {len(rows)} rows  "
          f"{'PASS' if not bad else 'FAIL ' + '; '.join(bad)}")
    fails += bool(bad)

    xml_rows = []
    for f in glob.glob(os.path.join(reports, "*.xml")):
        xml_rows += junit_xml.parse(open(f, "rb").read(), runner="junit")
    by_suffix_a = {r["key"].split("/")[-1]: r for r in rows}
    by_suffix_b = {r["key"].split("/")[-1]: r for r in xml_rows}
    mism = [k for k in by_suffix_a
            if k not in by_suffix_b
            or by_suffix_b[k]["status"] != by_suffix_a[k]["status"]
            or abs(by_suffix_b[k]["duration_ms"] - by_suffix_a[k]["duration_ms"]) > 50]
    print(f"  live jvm   Surefire XML (Tier B): {len(xml_rows)} rows, "
          f"cross-check vs listener {'PASS' if not mism else 'FAIL ' + str(mism)}")
    fails += bool(mism)

    # 2. ingest
    store_dir = os.path.join(SCRATCH_JVM, "store")
    os.makedirs(store_dir, exist_ok=True)
    st = Store(store_dir)
    run_id = st.start_run("junit", suite_sig=None)
    ctx = st.ctx_id(workers=1)
    n = st.ingest_ledger(ledger, run_id, ctx, "junit")
    st.refresh_aggregates()
    ok = n == len(rows)
    print(f"  live jvm   ingest → store: {n} rows  {'PASS' if ok else 'FAIL'}")
    fails += (not ok)

    # 3. SIGKILL checkpointing (MixedTest: fast, medium, then a 20s test)
    kill_ledger = os.path.join(SCRATCH_JVM, "kill.jsonl")
    proc = _junit_run(jar, classes, services, kill_ledger, "hh.live.MixedTest")
    time.sleep(6)
    try:
        os.killpg(proc.pid, 9)
    except ProcessLookupError:
        pass
    time.sleep(0.7)
    rows_k = [r for r in _rows(kill_ledger) if r.get("type") == "end"]
    suffs = {r["key"].split("/")[-1]: r["status"] for r in rows_k}
    ok = (suffs.get("fastOne") == "passed" and suffs.get("mediumTwo") == "passed"
          and "hangsAtEnd" not in suffs)
    print(f"  kill jvm    {len(rows_k)} rows checkpointed pre-SIGKILL "
          f"({suffs})  {'PASS' if ok else 'FAIL'}")
    fails += (not ok)
    return fails


if __name__ == "__main__":
    rc = 0
    if "--go" in sys.argv or "--all" in sys.argv:
        print("live Go (go test -json):")
        rc += live_go_matrix()
    if "--jvm" in sys.argv or "--all" in sys.argv:
        print("live JVM (JUnit Platform):")
        rc += live_jvm_matrix()
    sys.exit(1 if rc else 0)
