package java.util.concurrent.locks;
public class ReentrantReadWriteLock implements ReadWriteLock, java.io.Serializable {
    public ReentrantReadWriteLock() {}
    public ReentrantReadWriteLock(boolean fair) {}
    public Lock readLock() { return null; }
    public Lock writeLock() { return null; }
    public static class ReadLock implements Lock, java.io.Serializable {
        public void lock() {}
        public void lockInterruptibly() throws InterruptedException {}
        public boolean tryLock() { return false; }
        public boolean tryLock(long timeout, java.util.concurrent.TimeUnit unit) throws InterruptedException { return false; }
        public void unlock() {}
        public Condition newCondition() { return null; }
    }
    public static class WriteLock implements Lock, java.io.Serializable {
        public void lock() {}
        public void lockInterruptibly() throws InterruptedException {}
        public boolean tryLock() { return false; }
        public boolean tryLock(long timeout, java.util.concurrent.TimeUnit unit) throws InterruptedException { return false; }
        public void unlock() {}
        public Condition newCondition() { return null; }
    }
}
