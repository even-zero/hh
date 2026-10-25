package java.util.jar;
public class JarEntry extends java.util.zip.ZipEntry {
    public JarEntry(String name) { super(name); }
    public JarEntry(java.util.zip.ZipEntry entry) { super(entry.getName()); }
    public Attributes getAttributes() { return null; }
}
