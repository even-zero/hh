package java.nio.charset;
public abstract class Charset implements Comparable<Charset> {
    protected Charset(String canonicalName, String[] aliases) {}
    public static Charset forName(String charsetName) { return null; }
    public static Charset defaultCharset() { return null; }
    public final String name() { return null; }
    public int compareTo(Charset that) { return 0; }
}
