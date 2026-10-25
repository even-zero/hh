package java.util.stream;
public interface IntStream extends BaseStream<Integer, IntStream> {
    int sum();
    long count();
    java.util.OptionalInt min();
    java.util.OptionalInt max();
    boolean anyMatch(java.util.function.IntPredicate predicate);
    boolean allMatch(java.util.function.IntPredicate predicate);
    IntStream filter(java.util.function.IntPredicate predicate);
    IntStream map(java.util.function.IntUnaryOperator mapper);
    void forEach(java.util.function.IntConsumer action);
    Stream<Integer> boxed();
    <U> Stream<U> mapToObj(java.util.function.IntFunction<? extends U> mapper);
    LongStream asLongStream();
    static IntStream of(int value) { return null; }
    static IntStream of(int... values) { return null; }
    static IntStream range(int startInclusive, int endExclusive) { return null; }
}
