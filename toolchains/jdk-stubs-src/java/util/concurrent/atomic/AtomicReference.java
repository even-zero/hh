package java.util.concurrent.atomic;
public class AtomicReference<V> implements java.io.Serializable {
    public AtomicReference(V initialValue) {}
    public AtomicReference() {}
    public V get() { return null; }
    public void set(V newValue) {}
    public V getAndSet(V newValue) { return null; }
    public boolean compareAndSet(V expect, V update) { return false; }
}
