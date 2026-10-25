package java.util.stream;
public final class Collectors {
    private Collectors() {}
    public static <T> Collector<T, ?, java.util.List<T>> toList() { return null; }
    public static <T> Collector<T, ?, java.util.Set<T>> toSet() { return null; }
    public static <T, C extends java.util.Collection<T>> Collector<T, ?, C> toCollection(java.util.function.Supplier<C> collectionFactory) { return null; }
    public static <T, K, U> Collector<T, ?, java.util.Map<K, U>> toMap(java.util.function.Function<? super T, ? extends K> keyMapper, java.util.function.Function<? super T, ? extends U> valueMapper) { return null; }
    public static <T, K, U> Collector<T, ?, java.util.Map<K, U>> toMap(java.util.function.Function<? super T, ? extends K> keyMapper, java.util.function.Function<? super T, ? extends U> valueMapper, java.util.function.BinaryOperator<U> mergeFunction) { return null; }
    public static <T, K, U, M extends java.util.Map<K, U>> Collector<T, ?, M> toMap(java.util.function.Function<? super T, ? extends K> keyMapper, java.util.function.Function<? super T, ? extends U> valueMapper, java.util.function.BinaryOperator<U> mergeFunction, java.util.function.Supplier<M> mapSupplier) { return null; }
    public static <T> Collector<T, ?, String> joining() { return null; }
    public static <T> Collector<T, ?, String> joining(CharSequence delimiter) { return null; }
    public static <T> Collector<T, ?, String> joining(CharSequence delimiter, CharSequence prefix, CharSequence suffix) { return null; }
    public static <T, U, A, R> Collector<T, ?, R> mapping(java.util.function.Function<? super T, ? extends U> mapper, Collector<? super U, A, R> downstream) { return null; }
    public static <T, A, R, RR> Collector<T, ?, RR> collectingAndThen(Collector<T, A, R> downstream, java.util.function.Function<R, RR> finisher) { return null; }
    public static <T> Collector<T, ?, Long> counting() { return null; }
    public static <T> Collector<T, ?, java.util.Optional<T>> maxBy(java.util.Comparator<? super T> comparator) { return null; }
    public static <T> Collector<T, ?, java.util.Optional<T>> minBy(java.util.Comparator<? super T> comparator) { return null; }
    public static <T, K, A, D> Collector<T, ?, java.util.Map<K, D>> groupingBy(java.util.function.Function<? super T, ? extends K> classifier, Collector<? super T, A, D> downstream) { return null; }
    public static <T, K, D, A, M extends java.util.Map<K, D>> Collector<T, ?, M> groupingBy(java.util.function.Function<? super T, ? extends K> classifier, java.util.function.Supplier<M> mapFactory, Collector<? super T, A, D> downstream) { return null; }
    public static <T, K> Collector<T, ?, java.util.Map<K, java.util.List<T>>> groupingBy(java.util.function.Function<? super T, ? extends K> classifier) { return null; }
    public static <T, A, R> Collector<T, ?, R> filtering(java.util.function.Predicate<? super T> predicate, Collector<? super T, A, R> downstream) { return null; }
}
