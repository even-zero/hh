package java.net;
public final class URI implements Comparable<URI>, java.io.Serializable {
    public URI(String str) throws URISyntaxException {}
    public static URI create(String str) { return null; }
    public URI(String scheme, String host, String path, String fragment) throws URISyntaxException {}
    public String getScheme() { return null; }
    public String getPath() { return null; }
    public String getFragment() { return null; }
    public String getQuery() { return null; }
    public String getSchemeSpecificPart() { return null; }
    public String getRawPath() { return null; }
    public java.io.File toFile() { return null; }
    public URL toURL() throws MalformedURLException { return null; }
    public int compareTo(URI other) { return 0; }
}
