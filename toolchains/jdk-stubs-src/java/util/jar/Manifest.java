package java.util.jar;
public class Manifest implements Cloneable {
    public Manifest() {}
    public Manifest(java.io.InputStream is) throws java.io.IOException {}
    public Manifest(Manifest man) {}
    public Attributes getMainAttributes() { return null; }
    public java.util.Map<String, Attributes> getEntries() { return null; }
    public Attributes getAttributes(String name) { return null; }
}
