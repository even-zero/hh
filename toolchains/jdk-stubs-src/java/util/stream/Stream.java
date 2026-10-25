package java.util.stream;
public interface Stream<T> extends BaseStream<T, Stream<T>> {
    Stream<T> filter(java.util.function.Predicate<? super T> predicate);
    <R> Stream<R> map(java.util.function.Function<? super T, ? extends R> mapper);
    IntStream mapToInt(java.util.function.ToIntFunction<? super T> mapper);
    LongStream mapToLong(java.util.function.ToLongFunction<? super T> mapper);
    <R> Stream<R> flatMap(java.util.function.Function<? super T, ? extends Stream<? extends R>> mapper);
    Stream<T> distinct();
    Stream<T> sorted();
    Stream<T> sorted(java.util.Comparator<? super T> comparator);
    Stream<T> peek(java.util.function.Consumer<? super T> action);
    Stream<T> limit(long maxSize);
    Stream<T> skip(long n);
    void forEach(java.util.function.Consumer<? super T> action);
    void forEachOrdered(java.util.function.Consumer<? super T> action);
    Object[] toArray();
    <A> A[] toArray(java.util.function.IntFunction<A[]> generator);
    T reduce(T identity, java.util.function.BinaryOperator<T> accumulator);
    java.util.Optional<T> reduce(java.util.function.BinaryOperator<T> accumulator);
    <R> R reduce(R identity, java.util.function.BiFunction<R, ? super T, R> accumulator, java.util.function.BinaryOperator<R> combiner);
    <R> R collect(java.util.function.Supplier<R> supplier, java.util.function.BiConsumer<R, ? super T> accumulator, java.util.function.BiConsumer<R, R> combiner);
    <R, A> R collect(Collector<? super T, A, R> collector);
    java.util.Optional<T> min(java.util.Comparator<? super T> comparator);
    java.util.Optional<T> max(java.util.Comparator<? super T> comparator);
    long count();
    boolean anyMatch(java.util.function.Predicate<? super T> predicate);
    boolean allMatch(java.util.function.Predicate<? super T> predicate);
    boolean noneMatch(java.util.function.Predicate<? super T> predicate);
    java.util.Optional<T> findFirst();
    java.util.Optional<T> findAny();
    static <T> Stream<T> concat(Stream<? extends T> a, Stream<? extends T> b) { return null; }
    static <T> Stream<T> empty() { return null; }
    static <T> Stream<T> of(T t) { return null; }
    @SafeVarargs static <T> Stream<T> of(T... values) { return null; }
}
