package java.util.concurrent.locks;
public interface Condition {
    void await() throws InterruptedException;
    boolean await(long time, java.util.concurrent.TimeUnit unit) throws InterruptedException;
    void signal();
    void signalAll();
}
