package java.time;
public final class Duration implements Comparable<Duration>, java.time.temporal.TemporalAmount {
    public static final Duration ZERO = null;
    public static Duration ofMillis(long millis) { return null; }
    public static Duration ofSeconds(long seconds) { return null; }
    public static Duration ofSeconds(long seconds, long nanoAdjustment) { return null; }
    public static Duration ofNanos(long nanos) { return null; }
    public static Duration ofMinutes(long minutes) { return null; }
    public static Duration ofHours(long hours) { return null; }
    public static Duration ofDays(long days) { return null; }
    public static Duration between(java.time.temporal.Temporal startInclusive, java.time.temporal.Temporal endExclusive) { return null; }
    public long getSeconds() { return 0; }
    public int getNano() { return 0; }
    public long toMillis() { return 0; }
    public long toNanos() { return 0; }
    public long toSeconds() { return 0; }
    public Duration plus(Duration duration) { return null; }
    public Duration plusMillis(long millis) { return null; }
    public Duration plusSeconds(long seconds) { return null; }
    public Duration plusNanos(long nanos) { return null; }
    public Duration minus(Duration duration) { return null; }
    public Duration minusMillis(long millis) { return null; }
    public Duration minusSeconds(long seconds) { return null; }
    public Duration negated() { return null; }
    public Duration abs() { return null; }
    public int compareTo(Duration otherDuration) { return 0; }
    public boolean isZero() { return false; }
    public boolean isNegative() { return false; }
    public long get(java.time.temporal.TemporalUnit unit) { return 0; }
    public java.util.List<java.time.temporal.TemporalUnit> getUnits() { return null; }
}
