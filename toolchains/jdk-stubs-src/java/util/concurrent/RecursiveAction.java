package java.util.concurrent;
public abstract class RecursiveAction extends ForkJoinTask<Void> {
    protected abstract void compute();
    public Void getRawResult() { return null; }
    protected void setRawResult(Void value) {}
    protected boolean exec() { return false; }
}
