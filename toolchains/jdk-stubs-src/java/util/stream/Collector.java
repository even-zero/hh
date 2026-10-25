package java.util.stream;
public interface Collector<T, A, R> {
    java.util.function.Supplier<A> supplier();
    java.util.function.BiConsumer<A, T> accumulator();
    java.util.function.BinaryOperator<A> combiner();
    java.util.function.Function<A, R> finisher();
    java.util.Set<Collector.Characteristics> characteristics();
    enum Characteristics { CONCURRENT, UNORDERED, IDENTITY_FINISH }
}
