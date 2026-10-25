package java.nio.file;
public class SimpleFileVisitor<T> implements FileVisitor<T> {
    protected SimpleFileVisitor() {}
    public FileVisitResult preVisitDirectory(T dir, java.nio.file.attribute.BasicFileAttributes attrs) throws java.io.IOException { return null; }
    public FileVisitResult visitFile(T file, java.nio.file.attribute.BasicFileAttributes attrs) throws java.io.IOException { return null; }
    public FileVisitResult visitFileFailed(T file, java.io.IOException exc) throws java.io.IOException { return null; }
    public FileVisitResult postVisitDirectory(T dir, java.io.IOException exc) throws java.io.IOException { return null; }
}
