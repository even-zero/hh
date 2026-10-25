package java.util;
public final class OptionalInt {
    public static OptionalInt empty() { return null; }
    public static OptionalInt of(int value) { return null; }
    public int getAsInt() { return 0; }
    public boolean isPresent() { return false; }
    public void ifPresent(java.util.function.IntConsumer action) {}
    public int orElse(int other) { return 0; }
}
