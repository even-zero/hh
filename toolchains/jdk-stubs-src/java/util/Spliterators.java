package java.util;
public final class Spliterators {
    private Spliterators() {}
    public static <T> Spliterator<T> spliteratorUnknownSize(Iterator<? extends T> iterator, int characteristics) { return null; }
    public static <T> Spliterator<T> spliterator(Collection<? extends T> c, int characteristics) { return null; }
    public static <T> Spliterator<T> spliterator(T[] array, int characteristics) { return null; }
    public static <T> Spliterator<T> spliterator(List<T> list, int characteristics) { return null; }
    public static <T> Spliterator<T> emptySpliterator() { return null; }
}
