package java.util.concurrent;
public class ConcurrentLinkedQueue<E> extends java.util.AbstractQueue<E> implements java.util.Queue<E>, java.io.Serializable {
    public ConcurrentLinkedQueue() {}
    public ConcurrentLinkedQueue(java.util.Collection<? extends E> c) {}
    public boolean add(E e) { return false; }
    public boolean offer(E e) { return false; }
    public E poll() { return null; }
    public E peek() { return null; }
    public boolean isEmpty() { return false; }
    public int size() { return 0; }
    public boolean contains(Object o) { return false; }
    public boolean remove(Object o) { return false; }
    public java.util.Iterator<E> iterator() { return null; }
    public void forEach(java.util.function.Consumer<? super E> action) {}
    public boolean removeIf(java.util.function.Predicate<? super E> filter) { return false; }
    public java.util.stream.Stream<E> stream() { return null; }
    public java.util.stream.Stream<E> parallelStream() { return null; }
    public java.util.Spliterator<E> spliterator() { return null; }
}
