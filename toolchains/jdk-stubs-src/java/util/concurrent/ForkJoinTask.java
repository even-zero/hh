package java.util.concurrent;
public abstract class ForkJoinTask<V> implements Future<V>, java.io.Serializable {
    public abstract V getRawResult();
    protected abstract void setRawResult(V value);
    protected abstract boolean exec();
    public ForkJoinTask<V> fork() { return null; }
    public V join() { return null; }
    public V invoke() { return null; }
    public boolean cancel(boolean mayInterruptIfRunning) { return false; }
    public boolean isDone() { return false; }
    public boolean isCancelled() { return false; }
    public boolean isCompletedAbnormally() { return false; }
    public Throwable getException() { return null; }
    public V get() throws InterruptedException, ExecutionException { return null; }
    public V get(long timeout, TimeUnit unit) throws InterruptedException, ExecutionException, TimeoutException { return null; }
    public void reinitialize() {}
    public boolean tryUnfork() { return false; }
    public static int getSurplusQueuedTaskCount() { return 0; }
    public static ForkJoinPool getPool() { return null; }
    public int getForkJoinTaskTag() { return 0; }
}
