package java.util.stream;
public interface LongStream extends BaseStream<Long, LongStream> {
    long sum();
    long count();
    java.util.OptionalLong min();
    java.util.OptionalLong max();
    void forEach(java.util.function.LongConsumer action);
    Stream<Long> boxed();
    static LongStream of(long value) { return null; }
    static LongStream of(long... values) { return null; }
}
