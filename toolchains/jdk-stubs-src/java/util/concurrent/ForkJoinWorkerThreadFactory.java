package java.util.concurrent;
public interface ForkJoinWorkerThreadFactory {
    ForkJoinWorkerThread newThread(ForkJoinPool pool);
}
