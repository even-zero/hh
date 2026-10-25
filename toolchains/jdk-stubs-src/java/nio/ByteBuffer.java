package java.nio;
public abstract class ByteBuffer extends Buffer implements Comparable<ByteBuffer> {
    public static ByteBuffer allocate(int capacity) { return null; }
    public static ByteBuffer allocateDirect(int capacity) { return null; }
    public static ByteBuffer wrap(byte[] array) { return null; }
    public static ByteBuffer wrap(byte[] array, int offset, int length) { return null; }
    public abstract byte get();
    public abstract byte get(int index);
    public abstract ByteBuffer put(byte b);
    public abstract ByteBuffer put(int index, byte b);
    public ByteBuffer put(byte[] src) { return null; }
    public ByteBuffer put(byte[] src, int offset, int length) { return null; }
    public byte[] array() { return null; }
    public int arrayOffset() { return 0; }
    public boolean hasArray() { return false; }
    public int compareTo(ByteBuffer that) { return 0; }
    public java.nio.ByteOrder order() { return null; }
    public ByteBuffer order(java.nio.ByteOrder bo) { return null; }
}
