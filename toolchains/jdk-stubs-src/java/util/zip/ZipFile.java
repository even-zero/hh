package java.util.zip;
public class ZipFile implements ZipConstants, java.io.Closeable {
    public ZipFile(String name) throws java.io.IOException {}
    public ZipFile(java.io.File file) throws java.io.IOException {}
    public ZipFile(String name, int mode) throws java.io.IOException {}
    public java.util.Enumeration<? extends ZipEntry> entries() { return null; }
    public ZipEntry getEntry(String name) { return null; }
    public java.io.InputStream getInputStream(ZipEntry entry) throws java.io.IOException { return null; }
    public String getName() { return null; }
    public int size() { return 0; }
    public void close() throws java.io.IOException {}
}
