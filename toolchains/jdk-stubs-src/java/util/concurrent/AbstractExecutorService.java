package java.util.concurrent;
public abstract class AbstractExecutorService implements ExecutorService {
    public Future<?> submit(Runnable task) { return null; }
    public <T> Future<T> submit(Callable<T> task) { return null; }
    public <T> Future<T> submit(Runnable task, T result) { return null; }
    public <T> java.util.List<Future<T>> invokeAll(java.util.Collection<? extends Callable<T>> tasks) throws InterruptedException { return null; }
    public <T> java.util.List<Future<T>> invokeAll(java.util.Collection<? extends Callable<T>> tasks, long timeout, TimeUnit unit) throws InterruptedException { return null; }
    public <T> T invokeAny(java.util.Collection<? extends Callable<T>> tasks) throws InterruptedException, ExecutionException { return null; }
    public <T> T invokeAny(java.util.Collection<? extends Callable<T>> tasks, long timeout, TimeUnit unit) throws InterruptedException, ExecutionException, TimeoutException { return null; }
}
