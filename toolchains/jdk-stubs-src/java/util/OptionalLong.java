package java.util;
public final class OptionalLong {
    public static OptionalLong empty() { return null; }
    public static OptionalLong of(long value) { return null; }
    public long getAsLong() { return 0; }
    public boolean isPresent() { return false; }
    public void ifPresent(java.util.function.LongConsumer action) {}
    public long orElse(long other) { return 0; }
}
