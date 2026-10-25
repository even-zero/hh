package java.nio.file;
public interface FileVisitor<T> {
    FileVisitResult preVisitDirectory(T dir, java.nio.file.attribute.BasicFileAttributes attrs) throws java.io.IOException;
    FileVisitResult visitFile(T file, java.nio.file.attribute.BasicFileAttributes attrs) throws java.io.IOException;
    FileVisitResult visitFileFailed(T file, java.io.IOException exc) throws java.io.IOException;
    FileVisitResult postVisitDirectory(T dir, java.io.IOException exc) throws java.io.IOException;
}
