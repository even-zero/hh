package java.net;
public abstract class URLConnection {
    public java.io.InputStream getInputStream() throws java.io.IOException { return null; }
    public void connect() throws java.io.IOException {}
    public void setUseCaches(boolean useCaches) {}
    public boolean getUseCaches() { return false; }
    public void setRequestProperty(String key, String value) {}
    public String getContentType() { return null; }
    public int getContentLength() { return 0; }
}
