package java.util.concurrent;
public class ForkJoinPool extends AbstractExecutorService {
    public ForkJoinPool() {}
    public ForkJoinPool(int parallelism) {}
    public ForkJoinPool(int parallelism, ForkJoinWorkerThreadFactory factory, Thread.UncaughtExceptionHandler handler, boolean asyncMode) {}
    public <T> ForkJoinTask<T> submit(ForkJoinTask<T> task) { return null; }
    public <T> ForkJoinTask<T> submit(Callable<T> task) { return null; }
    public <T> ForkJoinTask<T> submit(Runnable task, T result) { return null; }
    public ForkJoinTask<?> submit(Runnable task) { return null; }
    public void execute(ForkJoinTask<?> task) {}
    public void execute(Runnable task) {}
    public <T> T invoke(ForkJoinTask<T> task) { return null; }
    public int getParallelism() { return 0; }
    public int getActiveThreadCount() { return 0; }
    public int getRunningThreadCount() { return 0; }
    public int getQueuedTaskCount() { return 0; }
    public int getQueuedSubmissionCount() { return 0; }
    public int getStealCount() { return 0; }
    public boolean isQuiescent() { return false; }
    public boolean isShutdown() { return false; }
    public boolean isTerminated() { return false; }
    public boolean awaitTermination(long timeout, TimeUnit unit) throws InterruptedException { return false; }
    public java.util.List<Runnable> shutdownNow() { return null; }
    public void shutdown() {}
    public static ForkJoinPool commonPool() { return null; }
    public static int getCommonPoolParallelism() { return 0; }
    public static void managedBlock(ManagedBlocker blocker) throws InterruptedException {}
    public interface ForkJoinWorkerThreadFactory extends java.util.concurrent.ForkJoinWorkerThreadFactory {}
    public static interface ManagedBlocker {
        boolean block() throws InterruptedException;
        boolean isReleasable();
    }
}
