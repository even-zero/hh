package java.nio;
public abstract class Buffer {
    public int capacity() { return 0; }
    public int position() { return 0; }
    public Buffer position(int newPosition) { return null; }
    public int limit() { return 0; }
    public Buffer limit(int newLimit) { return null; }
    public Buffer clear() { return null; }
    public Buffer flip() { return null; }
    public Buffer rewind() { return null; }
    public int remaining() { return 0; }
    public boolean hasRemaining() { return false; }
}
