package java.util.jar;
public class JarFile extends java.util.zip.ZipFile {
    public JarFile(String name) throws java.io.IOException { super(name); }
    public JarFile(java.io.File file) throws java.io.IOException { super(file); }
    public Manifest getManifest() throws java.io.IOException { return null; }
    public JarEntry getEntry(String name) { return null; }
    public java.util.Enumeration<JarEntry> entries() { return null; }
}
