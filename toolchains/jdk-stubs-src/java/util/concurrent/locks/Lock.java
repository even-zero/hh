package java.util.concurrent.locks;
public interface Lock {
    void lock();
    void lockInterruptibly() throws InterruptedException;
    boolean tryLock();
    boolean tryLock(long time, java.util.concurrent.TimeUnit unit) throws InterruptedException;
    void unlock();
    Condition newCondition();
}
