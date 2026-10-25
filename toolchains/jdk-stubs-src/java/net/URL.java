package java.net;
public final class URL implements java.io.Serializable {
    public URL(String spec) throws MalformedURLException {}
    public URL(String protocol, String host, String file) throws MalformedURLException {}
    public URL(String protocol, String host, int port, String file) throws MalformedURLException {}
    public String getProtocol() { return null; }
    public String getHost() { return null; }
    public String getFile() { return null; }
    public String getPath() { return null; }
    public String getQuery() { return null; }
    public String getRef() { return null; }
    public java.io.InputStream openStream() throws java.io.IOException { return null; }
    public java.net.URLConnection openConnection() throws java.io.IOException { return null; }
    public URI toURI() throws URISyntaxException { return null; }
}
