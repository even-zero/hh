package java.util.concurrent;
public class FutureTask<V> implements RunnableFuture<V> {
    public FutureTask(Callable<V> callable) {}
    public FutureTask(Runnable runnable, V result) {}
    public boolean cancel(boolean mayInterruptIfRunning) { return false; }
    public boolean isCancelled() { return false; }
    public boolean isDone() { return false; }
    public V get() throws InterruptedException, ExecutionException { return null; }
    public V get(long timeout, TimeUnit unit) throws InterruptedException, ExecutionException, TimeoutException { return null; }
    public void run() {}
}
