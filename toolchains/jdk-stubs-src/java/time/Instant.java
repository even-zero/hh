package java.time;
public final class Instant implements Comparable<Instant>, java.time.temporal.Temporal {
    public static Instant now() { return null; }
    public static Instant ofEpochMilli(long epochMilli) { return null; }
    public static Instant ofEpochSecond(long epochSecond) { return null; }
    public long getEpochSecond() { return 0; }
    public int getNano() { return 0; }
    public long toEpochMilli() { return 0; }
    public Instant plusSeconds(long secondsToAdd) { return null; }
    public Instant plusMillis(long millisToAdd) { return null; }
    public Instant plusNanos(long nanosToAdd) { return null; }
    public Instant minusSeconds(long secondsToSubtract) { return null; }
    public Instant minusMillis(long millisToSubtract) { return null; }
    public Instant minusNanos(long nanosToSubtract) { return null; }
    public boolean isBefore(Instant otherInstant) { return false; }
    public boolean isAfter(Instant otherInstant) { return false; }
    public int compareTo(Instant otherInstant) { return 0; }
    public boolean isSupported(java.time.temporal.TemporalField field) { return false; }
    public long getLong(java.time.temporal.TemporalField field) { return 0; }
    public java.time.temporal.Temporal plus(long amountToAdd, java.time.temporal.TemporalUnit unit) { return this; }
}
