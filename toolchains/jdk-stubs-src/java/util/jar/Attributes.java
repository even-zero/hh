package java.util.jar;
public class Attributes implements java.util.Map<Object, Object>, Cloneable {
    public Attributes() {}
    public Attributes(Attributes attr) {}
    public Attributes(int size) {}
    public Object get(Object name) { return null; }
    public String getValue(String name) { return null; }
    public String getValue(Attributes.Name name) { return null; }
    public String put(String name, String value) { return null; }
    public String putValue(String name, String value) { return null; }
    public int size() { return 0; }
    public boolean isEmpty() { return false; }
    public boolean containsKey(Object key) { return false; }
    public boolean containsValue(Object value) { return false; }
    public Object put(Object key, Object value) { return null; }
    public Object remove(Object key) { return null; }
    public void putAll(java.util.Map<?, ?> t) {}
    public void clear() {}
    public java.util.Set<Object> keySet() { return null; }
    public java.util.Collection<Object> values() { return null; }
    public java.util.Set<java.util.Map.Entry<Object, Object>> entrySet() { return null; }
    public boolean equals(Object o) { return false; }
    public int hashCode() { return 0; }
    public static class Name {
        public Name(String name) {}
        public static final Name MANIFEST_VERSION = new Name("Manifest-Version");
        public static final Name MAIN_CLASS = new Name("Main-Class");
    }
}
