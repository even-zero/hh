package hh;

import java.io.FileWriter;
import java.io.IOException;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.launcher.TestExecutionListener;
import org.junit.platform.launcher.TestIdentifier;

/**
 * Tier A adapter: JUnit Platform listener → hh ledger (streaming).
 *
 * Register via ServiceLoader — a file
 *   META-INF/services/org.junit.platform.launcher.TestExecutionListener
 * containing the line `hh.HhJUnitListener` — and set HH_LEDGER.
 * Rows are flushed one per finished test, so a SIGKILL mid-suite keeps every
 * finished test checkpointed (Law 2: capture never blocks tests).
 *
 * Java 8+ / JUnit Platform 1.x compatible (no switch expressions).
 */
public class HhJUnitListener implements TestExecutionListener {
    private static final Map<String, Long> START = new ConcurrentHashMap<String, Long>();

    /** Mirror of hh/keys.py normalize(): drop the class-level segment. */
    private static String norm(String raw) {
        String r = raw.replace("\\", "/").replace("::", "/").replace(".", "/");
        String[] parts = r.split("/");
        if (parts.length >= 3 && parts[parts.length - 2].length() > 0
                && Character.isUpperCase(parts[parts.length - 2].charAt(0))) {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < parts.length; i++) {
                if (i == parts.length - 2) continue;
                if (sb.length() > 0) sb.append('/');
                sb.append(parts[i]);
            }
            r = sb.toString();
        }
        return "junit::" + r;
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static void write(String jsonLine) {
        String ledger = System.getenv("HH_LEDGER");
        if (ledger == null || ledger.isEmpty()) return;
        FileWriter fw = null;
        try {
            fw = new FileWriter(ledger, true);     // append + close = flushed
            fw.write(jsonLine + "\n");
        } catch (IOException e) {
            // Law 2: capture trouble must never fail the test run
        } finally {
            if (fw != null) { try { fw.close(); } catch (IOException ignore) {} }
        }
    }

    private static String classAndMethod(TestIdentifier id) {
        String uid = id.getUniqueId();
        String cls = "", meth = "";
        int i = uid.indexOf("[class:");
        if (i >= 0) {
            int j = uid.indexOf(']', i);
            if (j > i) cls = uid.substring(i + 7, j);
        }
        i = uid.indexOf("[method:");
        if (i >= 0) {
            int j = uid.indexOf(']', i);
            if (j > i) {
                meth = uid.substring(i + 8, j);
                int k = meth.indexOf('(');
                if (k > 0) meth = meth.substring(0, k);
            }
        }
        if (cls.isEmpty()) return id.getDisplayName();
        return meth.isEmpty() ? cls + "." + id.getDisplayName() : cls + "." + meth;
    }

    @Override
    public void executionStarted(TestIdentifier id) {
        if (id.isTest()) START.put(id.getUniqueId(), System.nanoTime());
    }

    @Override
    public void executionFinished(TestIdentifier id, TestExecutionResult result) {
        if (!id.isTest()) return;
        Long t0 = START.remove(id.getUniqueId());
        double ms = t0 == null ? 0.0 : (System.nanoTime() - t0) / 1e6;
        TestExecutionResult.Status s = result.getStatus();
        String status;
        if (s == TestExecutionResult.Status.SUCCESSFUL) status = "passed";
        else if (s == TestExecutionResult.Status.ABORTED) status = "skipped";
        else status = "failed";
        String raw = classAndMethod(id);
        write("{\"type\":\"end\",\"key\":\"" + esc(norm(raw)) + "\",\"raw_id\":\""
                + esc(raw) + "\",\"status\":\"" + status + "\",\"duration_ms\":"
                + String.format(Locale.US, "%.3f", ms) + ",\"censored\":false}");
    }
}
