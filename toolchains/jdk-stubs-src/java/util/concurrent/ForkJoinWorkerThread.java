package java.util.concurrent;
public abstract class ForkJoinWorkerThread extends Thread {
    protected ForkJoinWorkerThread(ForkJoinPool pool) {}
    public ForkJoinPool getPool() { return null; }
    public int getPoolIndex() { return 0; }
}
