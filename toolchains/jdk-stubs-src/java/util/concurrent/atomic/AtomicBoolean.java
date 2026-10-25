package java.util.concurrent.atomic;
public class AtomicBoolean implements java.io.Serializable {
    public AtomicBoolean(boolean initialValue) {}
    public AtomicBoolean() {}
    public boolean get() { return false; }
    public void set(boolean newValue) {}
    public boolean getAndSet(boolean newValue) { return false; }
    public boolean compareAndSet(boolean expect, boolean update) { return false; }
}
