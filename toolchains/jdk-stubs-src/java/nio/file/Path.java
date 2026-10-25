package java.nio.file;
public interface Path extends Comparable<Path>, Iterable<Path> {
    Path resolve(Path other);
    Path resolve(String other);
    Path resolveSibling(Path other);
    Path getParent();
    Path getFileName();
    Path normalize();
    Path toAbsolutePath();
    java.net.URI toUri();
    java.io.File toFile();
    boolean startsWith(Path other);
    boolean endsWith(Path other);
    boolean endsWith(String other);
    Path relativize(Path other);
    Path toRealPath(java.nio.file.LinkOption... options) throws java.io.IOException;
    boolean isEmpty();
    FileSystem getFileSystem();
}
