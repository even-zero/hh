package java.nio.file;
public interface DirectoryStream<T> extends java.io.Closeable, Iterable<T> {
    interface Filter<T> { boolean accept(T entry) throws java.io.IOException; }
}
