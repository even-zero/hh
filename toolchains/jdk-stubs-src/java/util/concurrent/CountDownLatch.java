package java.util.concurrent;
public class CountDownLatch {
    public CountDownLatch(int count) {}
    public void await() throws InterruptedException {}
    public boolean await(long timeout, TimeUnit unit) throws InterruptedException { return false; }
    public void countDown() {}
    public long getCount() { return 0; }
}
