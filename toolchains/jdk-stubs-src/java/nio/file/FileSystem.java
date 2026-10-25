package java.nio.file;
public abstract class FileSystem {
    public abstract Path getPath(String first, String... more);
    public abstract PathMatcher getPathMatcher(String syntaxAndPattern);
    public abstract Iterable<Path> getRootDirectories();
    public abstract boolean isOpen();
    public String getSeparator() { return null; }
    public abstract void close() throws java.io.IOException;
    public static FileSystem getDefault() { return null; }
}
