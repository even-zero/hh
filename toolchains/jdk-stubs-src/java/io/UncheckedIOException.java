package java.io;
public class UncheckedIOException extends RuntimeException {
    public UncheckedIOException(IOException cause) {}
    public UncheckedIOException(String message, IOException cause) {}
    public IOException getCause() { return null; }
}
