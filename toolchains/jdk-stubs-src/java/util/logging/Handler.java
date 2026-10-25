package java.util.logging;
public abstract class Handler {
    public abstract void publish(LogRecord record);
    public abstract void flush();
    public abstract void close() throws SecurityException;
    public void setLevel(Level newLevel) throws SecurityException {}
    public Level getLevel() { return null; }
}
