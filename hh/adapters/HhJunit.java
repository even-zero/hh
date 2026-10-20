/*
 * hh's JUnit Platform driver: discovery + ledger-streaming execution.
 *
 * hh launches a test suite, kills it if it overruns its budget, and resumes
 * from whatever finished. That needs two things the JUnit console launcher does
 * not give us: a per-test ledger written *during* the run (so a SIGKILL keeps
 * the finished rows) and the ability to run an explicit set of test ids.
 *
 * This class is compiled once by hh/adapters/junit_stream.py and then run:
 *
 *   java -cp <driver>:<project test classpath> HhJunit discover \
 *        --scan target/test-classes --out ids.jsonl
 *   java -cp <driver>:<project test classpath> HhJunit run \
 *        --scan target/test-classes --ledger ledger.jsonl [--ids selection.jsonl]
 *
 * It works for any test framework that speaks the JUnit Platform: JUnit 5
 * (Jupiter), JUnit 4 through the Vintage engine, and Kotlin test suites
 * compiled against either of them.
 *
 * Ledger rows and the identity scheme mirror hh/adapters/base.py and
 * hh/keys.py exactly; tests/test_junit_adapter.py pins that they agree.
 */

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.platform.engine.DiscoverySelector;
import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.engine.discovery.DiscoverySelectors;
import org.junit.platform.launcher.Launcher;
import org.junit.platform.launcher.LauncherDiscoveryRequest;
import org.junit.platform.launcher.TestExecutionListener;
import org.junit.platform.launcher.TestIdentifier;
import org.junit.platform.launcher.TestPlan;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;

public final class HhJunit {

    private static final Pattern SEGMENT = Pattern.compile("\\[([a-zA-Z-]+):([^\\]]*)\\]");
    private static final Pattern JSON_RAW_ID =
            Pattern.compile("\"raw_id\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");

    private HhJunit() {
    }

    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("usage: HhJunit discover|run --scan <dirs> ...");
            System.exit(2);
        }
        Map<String, String> opts = parseOptions(args, 1);
        try {
            List<Path> roots = scanRoots(opts.get("--scan"));
            if (roots.isEmpty()) {
                System.err.println("hh: no compiled test classes to scan");
                System.exit(2);
            }
            Launcher launcher = LauncherFactory.create();
            TestPlan plan = launcher.discover(rootRequest(roots));
            List<TestIdentifier> tests = collectTests(plan);
            if ("discover".equals(args[0])) {
                discover(tests, opts.get("--out"));
            } else if ("run".equals(args[0])) {
                System.exit(run(launcher, plan, tests, roots, opts));
            } else {
                System.err.println("hh: unknown mode " + args[0]);
                System.exit(2);
            }
        } catch (Exception e) {
            System.err.println("hh: " + e);
            System.exit(2);
        }
    }

    // -- options ----------------------------------------------------------

    private static Map<String, String> parseOptions(String[] args, int from) {
        Map<String, String> opts = new HashMap<>();
        for (int i = from; i + 1 < args.length; i += 2) {
            opts.put(args[i], args[i + 1]);
        }
        return opts;
    }

    private static List<Path> scanRoots(String spec) {
        List<Path> roots = new ArrayList<>();
        if (spec == null) {
            return roots;
        }
        for (String part : spec.split(Pattern.quote(java.io.File.pathSeparator))) {
            if (part.isEmpty()) {
                continue;
            }
            Path p = Paths.get(part).toAbsolutePath();
            if (Files.isDirectory(p)) {
                roots.add(p);
            }
        }
        return roots;
    }

    private static LauncherDiscoveryRequest rootRequest(List<Path> roots) {
        return LauncherDiscoveryRequestBuilder.request()
                .selectors(DiscoverySelectors.selectClasspathRoots(new HashSet<>(roots)))
                .build();
    }

    private static List<TestIdentifier> collectTests(TestPlan plan) {
        List<TestIdentifier> out = new ArrayList<>();
        for (TestIdentifier root : plan.getRoots()) {
            walk(plan, root, out);
        }
        return out;
    }

    private static void walk(TestPlan plan, TestIdentifier id, List<TestIdentifier> out) {
        if (id.isTest()) {
            out.add(id);
            return;
        }
        for (TestIdentifier child : plan.getChildren(id)) {
            walk(plan, child, out);
        }
    }

    // -- discover ---------------------------------------------------------

    private static void discover(List<TestIdentifier> tests, String out) throws IOException {
        if (out == null) {
            throw new IllegalArgumentException("discover needs --out");
        }
        Map<String, String> keys = keyMap(tests);
        try (BufferedWriter w = Files.newBufferedWriter(Paths.get(out), StandardCharsets.UTF_8)) {
            for (TestIdentifier t : tests) {
                String raw = rawId(t);
                w.write("{\"raw_id\":" + jstr(raw) + ",\"key\":" + jstr(keys.get(raw)) + "}\n");
            }
        }
        System.err.println("hh: discovered " + tests.size() + " tests");
    }

    // -- run --------------------------------------------------------------

    private static int run(Launcher launcher, TestPlan plan, List<TestIdentifier> tests,
                           List<Path> roots, Map<String, String> opts) throws IOException {
        String ledger = opts.get("--ledger");
        if (ledger == null) {
            throw new IllegalArgumentException("run needs --ledger");
        }
        Map<String, String> keyOf = keyMap(tests);
        List<DiscoverySelector> selectors = new ArrayList<>();
        int missing = 0;
        String idsFile = opts.get("--ids");
        if (idsFile == null) {
            // Record pass: run what the classpath roots hold, exactly like the
            // plain runner would, and stream the ledger while doing it.
            selectors.addAll(DiscoverySelectors.selectClasspathRoots(new HashSet<>(roots)));
        } else {
            Map<String, String> uniqueOf = new LinkedHashMap<>();
            for (TestIdentifier t : tests) {
                uniqueOf.putIfAbsent(rawId(t), t.getUniqueId());
            }
            for (String raw : readRawIds(Paths.get(idsFile))) {
                String uid = uniqueOf.get(raw);
                if (uid == null) {
                    missing++;
                    continue;
                }
                selectors.add(DiscoverySelectors.selectUniqueId(uid));
            }
            if (missing > 0) {
                System.err.println("hh: " + missing + " requested ids are not in this suite");
            }
            if (selectors.isEmpty()) {
                writeSuiteEnd(ledger, 0);
                return 0;
            }
        }
        LauncherDiscoveryRequest request = LauncherDiscoveryRequestBuilder.request()
                .selectors(selectors.toArray(new DiscoverySelector[0]))
                .build();
        Ledger listener = new Ledger(ledger, keyOf);
        launcher.execute(request, listener);
        listener.suiteEnd();
        System.err.println("hh: ran " + listener.finished + " tests, "
                + listener.failed + " failed");
        return listener.failed > 0 ? 1 : 0;
    }

    private static List<String> readRawIds(Path file) throws IOException {
        List<String> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
            if (line.trim().isEmpty()) {
                continue;
            }
            Matcher m = JSON_RAW_ID.matcher(line);
            String raw = m.find() ? unescape(m.group(1)) : line.trim();
            if (!raw.isEmpty() && seen.add(raw)) {
                out.add(raw);
            }
        }
        return out;
    }

    // -- the listener -----------------------------------------------------

    private static final class Ledger implements TestExecutionListener {
        private final BufferedWriter out;
        private final Map<String, String> keyOf;
        private final Map<String, Long> started = new HashMap<>();
        private final Set<String> ended = new HashSet<>();
        int finished;
        int failed;

        Ledger(String path, Map<String, String> keyOf) throws IOException {
            this.out = Files.newBufferedWriter(Paths.get(path), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND,
                    StandardOpenOption.WRITE);
            this.keyOf = keyOf;
        }

        /** Key for a test. Dynamic tests (@TestFactory) do not exist at plan
         *  time, so they fall back to the plain normalization. */
        private String keyFor(String raw) {
            String key = keyOf.get(raw);
            return key != null ? key : normalize(raw);
        }

        @Override
        public void executionStarted(TestIdentifier id) {
            if (!id.isTest()) {
                return;
            }
            String raw = rawId(id);
            started.put(raw, System.nanoTime());
            row("{\"type\":\"start\",\"key\":" + jstr(keyFor(raw))
                    + ",\"raw_id\":" + jstr(raw) + ",\"ts\":" + now() + "}");
        }

        @Override
        public void executionFinished(TestIdentifier id, TestExecutionResult result) {
            if (!id.isTest()) {
                return;
            }
            String status;
            switch (result.getStatus()) {
                case SUCCESSFUL:
                    status = "passed";
                    break;
                case ABORTED:
                    status = "failed";
                    break;
                case FAILED:
                default:
                    status = "failed";
                    break;
            }
            end(id, status);
        }

        @Override
        public void executionSkipped(TestIdentifier id, String reason) {
            if (id.isTest()) {
                end(id, "skipped");
            }
        }

        private void end(TestIdentifier id, String status) {
            String raw = rawId(id);
            if (!ended.add(raw)) {
                return;                                 // no double rows
            }
            Long t0 = started.remove(raw);
            double ms = t0 == null ? 0.0 : (System.nanoTime() - t0) / 1_000_000.0;
            row("{\"type\":\"end\",\"key\":" + jstr(keyFor(raw))
                    + ",\"raw_id\":" + jstr(raw) + ",\"status\":" + jstr(status)
                    + ",\"duration_ms\":" + round3(ms) + ",\"censored\":false"
                    + ",\"ts\":" + now() + "}");
            finished++;
            if ("failed".equals(status)) {
                failed++;
            }
        }

        void suiteEnd() {
            try {
                out.write("{\"type\":\"suite_end\",\"tests\":" + finished
                        + ",\"ts\":" + now() + "}\n");
                out.flush();
                out.close();
            } catch (IOException e) {
                System.err.println("hh: could not seal the ledger: " + e);
            }
        }

        private void row(String line) {
            try {
                out.write(line + "\n");
                out.flush();        // a SIGKILL must not lose finished tests
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }
    }

    private static void writeSuiteEnd(String ledger, int tests) throws IOException {
        try (BufferedWriter w = Files.newBufferedWriter(Paths.get(ledger), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND, StandardOpenOption.WRITE)) {
            w.write("{\"type\":\"suite_end\",\"tests\":" + tests + ",\"ts\":" + now() + "}\n");
        }
    }

    // -- identity: the same scheme as hh/keys.py --------------------------

    /**
     * A readable, stable id for one test: {@code com.example.FooTest.testAdd}.
     *
     * Built from the unique id (whose segment grammar is stable across JUnit
     * versions) rather than from source objects. Overloaded methods get a
     * parameter hash, and each invocation of a template or factory gets the
     * invocation suffix, so the id is unique inside the suite.
     */
    static String rawId(TestIdentifier id) {
        String uid = id.getUniqueId();
        String cls = null;
        String method = null;
        String params = null;
        String suffix = "";
        Matcher m = SEGMENT.matcher(uid);
        while (m.find()) {
            String type = m.group(1);
            String value = m.group(2);
            if ("class".equals(type)) {
                cls = value;
            } else if ("nested-class".equals(type)) {
                cls = cls == null ? value : cls + "$" + value;
            } else if ("method".equals(type) || "test-factory".equals(type)
                    || "test-template".equals(type)) {
                int paren = value.indexOf('(');
                method = paren >= 0 ? value.substring(0, paren) : value;
                params = paren >= 0 ? value.substring(paren + 1, value.length() - 1) : "";
            } else if ("test-template-invocation".equals(type) || "dynamic-test".equals(type)) {
                suffix = "[" + value + "]";
            }
        }
        String base;
        if (cls != null && method != null) {
            base = cls + "." + method;
        } else if (cls != null) {
            base = cls;
        } else {
            base = id.getDisplayName();
        }
        if (params != null && !params.isEmpty()) {
            // overloads share a method name; the types are what tell them apart
            base += "#" + sha1(params).substring(0, 6);
        }
        return base + suffix;
    }

    private static Map<String, String> keyMap(List<TestIdentifier> tests) {
        List<String> raws = new ArrayList<>();
        for (TestIdentifier t : tests) {
            raws.add(rawId(t));
        }
        Map<String, Integer> counts = new HashMap<>();
        for (String raw : raws) {
            String k = normalize(raw);
            Integer n = counts.get(k);
            counts.put(k, n == null ? 1 : n + 1);
        }
        Map<String, String> out = new LinkedHashMap<>();
        for (String raw : raws) {
            String key = normalize(raw);
            if (counts.get(key) > 1) {
                key += " :: collision-" + sha1(raw).substring(0, 12);
            }
            out.put(raw, key);
        }
        return out;
    }

    /** hh/keys.normalize for runner "junit": dots are path separators, the
     *  class segment is kept (in Java the class is the file), a trailing
     *  [invocation] becomes an 8-hex parameter tag. */
    static String normalize(String rawId) {
        String raw = rawId.trim();
        String params = "";
        int bracket = raw.lastIndexOf('[');
        if (bracket >= 0 && raw.endsWith("]")) {
            params = sha1(raw.substring(bracket + 1, raw.length() - 1)).substring(0, 8);
            raw = raw.substring(0, bracket);
        }
        raw = raw.replace("::", "/").replace(".", "/").replace("\\", "/");
        raw = raw.replaceAll("/+", "/");
        while (raw.startsWith("/")) {
            raw = raw.substring(1);
        }
        while (raw.endsWith("/")) {
            raw = raw.substring(0, raw.length() - 1);
        }
        String key = "junit::" + raw;
        if (!params.isEmpty()) {
            key += " :: " + params;
        }
        return key;
    }

    // -- small helpers ----------------------------------------------------

    private static String sha1(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            byte[] digest = md.digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte b : digest) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static double now() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static double round3(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }

    private static String jstr(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    b.append("\\\"");
                    break;
                case '\\':
                    b.append("\\\\");
                    break;
                case '\n':
                    b.append("\\n");
                    break;
                case '\r':
                    b.append("\\r");
                    break;
                case '\t':
                    b.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        return b.append('"').toString();
    }

    private static String unescape(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\' && i + 1 < s.length()) {
                char n = s.charAt(++i);
                switch (n) {
                    case 'n':
                        b.append('\n');
                        break;
                    case 'r':
                        b.append('\r');
                        break;
                    case 't':
                        b.append('\t');
                        break;
                    default:
                        b.append(n);
                }
            } else {
                b.append(c);
            }
        }
        return b.toString();
    }
}
