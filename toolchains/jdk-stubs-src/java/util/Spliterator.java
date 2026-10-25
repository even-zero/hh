package java.util;
public interface Spliterator<T> {
    boolean tryAdvance(java.util.function.Consumer<? super T> action);
    Spliterator<T> trySplit();
    long estimateSize();
    long getExactSizeIfKnown();
    int characteristics();
    boolean hasCharacteristics(int characteristics);
    Comparator<? super T> getComparator();
    static final int ORDERED = 0x00000010;
    static final int DISTINCT = 0x00000001;
    static final int SORTED = 0x00000004;
    static final int SIZED = 0x00000040;
    static final int NONNULL = 0x00000100;
    static final int IMMUTABLE = 0x00000400;
    static final int CONCURRENT = 0x00001000;
    static final int SUBSIZED = 0x00004000;
}
