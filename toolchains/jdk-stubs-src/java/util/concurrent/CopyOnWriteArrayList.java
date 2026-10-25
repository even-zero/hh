package java.util.concurrent;
public class CopyOnWriteArrayList<E> implements java.util.List<E>, Cloneable, java.io.Serializable {
    public CopyOnWriteArrayList() {}
    public CopyOnWriteArrayList(java.util.Collection<? extends E> c) {}
    public CopyOnWriteArrayList(E[] toCopyIn) {}
    public int size() { return 0; }
    public boolean isEmpty() { return false; }
    public boolean contains(Object o) { return false; }
    public int indexOf(Object o) { return 0; }
    public int lastIndexOf(Object o) { return 0; }
    public Object clone() { return null; }
    public Object[] toArray() { return null; }
    public <T> T[] toArray(T[] a) { return null; }
    public E get(int index) { return null; }
    public E set(int index, E element) { return null; }
    public boolean add(E e) { return false; }
    public void add(int index, E element) {}
    public E remove(int index) { return null; }
    public boolean remove(Object o) { return false; }
    public boolean addAll(java.util.Collection<? extends E> c) { return false; }
    public boolean addAll(int index, java.util.Collection<? extends E> c) { return false; }
    public boolean removeAll(java.util.Collection<?> c) { return false; }
    public boolean retainAll(java.util.Collection<?> c) { return false; }
    public void clear() {}
    public java.util.Iterator<E> iterator() { return null; }
    public java.util.ListIterator<E> listIterator() { return null; }
    public java.util.ListIterator<E> listIterator(int index) { return null; }
    public java.util.List<E> subList(int fromIndex, int toIndex) { return null; }
    public boolean containsAll(java.util.Collection<?> c) { return false; }
    public boolean equals(Object o) { return false; }
    public int hashCode() { return 0; }
    public void forEach(java.util.function.Consumer<? super E> action) {}
    public boolean removeIf(java.util.function.Predicate<? super E> filter) { return false; }
    public java.util.stream.Stream<E> stream() { return null; }
    public java.util.stream.Stream<E> parallelStream() { return null; }
    public java.util.Spliterator<E> spliterator() { return null; }
}
