package java.util.concurrent;
public abstract class RecursiveTask<V> extends ForkJoinTask<V> {
    protected abstract V compute();
    public V getRawResult() { return null; }
    protected void setRawResult(V value) {}
    protected boolean exec() { return false; }
}
