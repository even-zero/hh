package java.time;
public final class LocalDateTime implements Comparable<LocalDateTime>, java.time.temporal.Temporal {
    public static LocalDateTime now() { return null; }
    public static LocalDateTime of(LocalDate date, LocalTime time) { return null; }
    public static LocalDateTime of(int year, int month, int dayOfMonth, int hour, int minute) { return null; }
    public static LocalDateTime ofInstant(Instant instant, ZoneOffset zone) { return null; }
    public LocalDate toLocalDate() { return null; }
    public LocalTime toLocalTime() { return null; }
    public Instant toInstant(ZoneOffset offset) { return null; }
    public LocalDateTime plusNanos(long nanos) { return null; }
    public LocalDateTime plusSeconds(long seconds) { return null; }
    public LocalDateTime plusMinutes(long minutes) { return null; }
    public int compareTo(LocalDateTime other) { return 0; }
    public boolean isSupported(java.time.temporal.TemporalField field) { return false; }
    public long getLong(java.time.temporal.TemporalField field) { return 0; }
    public java.time.temporal.Temporal plus(long amountToAdd, java.time.temporal.TemporalUnit unit) { return this; }
}
