package java.util.zip;
public class ZipEntry implements ZipConstants, Cloneable {
    public ZipEntry(String name) {}
    public ZipEntry(ZipEntry e) {}
    public String getName() { return null; }
    public long getTime() { return 0; }
    public void setTime(long time) {}
    public long getSize() { return 0; }
    public void setSize(long size) {}
    public long getCompressedSize() { return 0; }
    public long getCrc() { return 0; }
    public int getMethod() { return 0; }
    public boolean isDirectory() { return false; }
    public String getComment() { return null; }
}
