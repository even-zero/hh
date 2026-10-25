package java.util;
public final class Optional<T> {
    public static <T> Optional<T> empty() { return null; }
    public static <T> Optional<T> of(T value) { return null; }
    public static <T> Optional<T> ofNullable(T value) { return null; }
    public T get() { return null; }
    public boolean isPresent() { return false; }
    public boolean isEmpty() { return false; }
    public void ifPresent(java.util.function.Consumer<? super T> action) {}
    public <U> Optional<U> map(java.util.function.Function<? super T, ? extends U> mapper) { return null; }
    public <U> Optional<U> flatMap(java.util.function.Function<? super T, ? extends Optional<? extends U>> mapper) { return null; }
    public T orElse(T other) { return null; }
    public T orElseGet(java.util.function.Supplier<? extends T> other) { return null; }
    public <X extends Throwable> T orElseThrow(java.util.function.Supplier<? extends X> exceptionSupplier) throws X { return null; }
    public Optional<T> filter(java.util.function.Predicate<? super T> predicate) { return null; }
}
