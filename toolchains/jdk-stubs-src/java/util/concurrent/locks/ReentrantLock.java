package java.util.concurrent.locks;
public class ReentrantLock implements Lock, java.io.Serializable {
    public ReentrantLock() {}
    public ReentrantLock(boolean fair) {}
    public void lock() {}
    public void lockInterruptibly() throws InterruptedException {}
    public boolean tryLock() { return false; }
    public boolean tryLock(long timeout, java.util.concurrent.TimeUnit unit) throws InterruptedException { return false; }
    public void unlock() {}
    public Condition newCondition() { return null; }
    public boolean isLocked() { return false; }
}
