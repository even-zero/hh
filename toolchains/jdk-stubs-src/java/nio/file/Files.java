package java.nio.file;
import java.io.InputStream; import java.io.OutputStream;
public final class Files {
    private Files() {}
    public static boolean exists(Path path, java.nio.file.LinkOption... options) { return false; }
    public static boolean isDirectory(Path path, java.nio.file.LinkOption... options) { return false; }
    public static boolean isRegularFile(Path path, java.nio.file.LinkOption... options) { return false; }
    public static InputStream newInputStream(Path path, java.nio.file.OpenOption... options) throws java.io.IOException { return null; }
    public static OutputStream newOutputStream(Path path, java.nio.file.OpenOption... options) throws java.io.IOException { return null; }
    public static byte[] readAllBytes(Path path) throws java.io.IOException { return null; }
    public static java.util.List<String> readAllLines(Path path) throws java.io.IOException { return null; }
    public static java.util.List<String> readAllLines(Path path, java.nio.charset.Charset cs) throws java.io.IOException { return null; }
    public static Path write(Path path, byte[] bytes, java.nio.file.OpenOption... options) throws java.io.IOException { return null; }
    public static Path write(Path path, java.lang.Iterable<? extends CharSequence> lines, java.nio.charset.Charset cs, java.nio.file.OpenOption... options) throws java.io.IOException { return null; }
    public static DirectoryStream<Path> newDirectoryStream(Path dir) throws java.io.IOException { return null; }
    public static DirectoryStream<Path> newDirectoryStream(Path dir, String glob) throws java.io.IOException { return null; }
    public static Path createFile(Path path, java.nio.file.attribute.FileAttribute<?>... attrs) throws java.io.IOException { return null; }
    public static Path createDirectory(Path dir, java.nio.file.attribute.FileAttribute<?>... attrs) throws java.io.IOException { return null; }
    public static Path createDirectories(Path dir, java.nio.file.attribute.FileAttribute<?>... attrs) throws java.io.IOException { return null; }
    public static Path createTempFile(String prefix, String suffix, java.nio.file.attribute.FileAttribute<?>... attrs) throws java.io.IOException { return null; }
    public static Path createTempDirectory(String prefix, java.nio.file.attribute.FileAttribute<?>... attrs) throws java.io.IOException { return null; }
    public static Path copy(Path source, Path target, java.nio.file.CopyOption... options) throws java.io.IOException { return null; }
    public static Path move(Path source, Path target, java.nio.file.CopyOption... options) throws java.io.IOException { return null; }
    public static boolean deleteIfExists(Path path) throws java.io.IOException { return false; }
    public static void delete(Path path) throws java.io.IOException {}
    public static boolean isHidden(Path path) throws java.io.IOException { return false; }
    public static long size(Path path) throws java.io.IOException { return 0; }
    public static Path walkFileTree(Path start, FileVisitor<? super Path> visitor) throws java.io.IOException { return null; }
    public static Path walkFileTree(Path start, java.util.Set<FileVisitOption> options, int maxDepth, FileVisitor<? super Path> visitor) throws java.io.IOException { return null; }
    public static java.util.stream.Stream<Path> list(Path dir) throws java.io.IOException { return null; }
    public static java.util.stream.Stream<Path> walk(Path start, int maxDepth, java.nio.file.FileVisitOption... options) throws java.io.IOException { return null; }
    public static String readString(Path path, java.nio.charset.Charset cs) throws java.io.IOException { return null; }
    public static java.io.BufferedWriter newBufferedWriter(Path path, java.nio.charset.Charset cs, java.nio.file.OpenOption... options) throws java.io.IOException { return null; }
    public static java.io.BufferedReader newBufferedReader(Path path, java.nio.charset.Charset cs) throws java.io.IOException { return null; }
    public static java.util.stream.Stream<Path> find(Path start, int maxDepth, java.util.function.BiPredicate<Path, java.nio.file.attribute.BasicFileAttributes> matcher, java.nio.file.FileVisitOption... options) throws java.io.IOException { return null; }
    public static long getLastModifiedTime(Path path, java.nio.file.LinkOption... options) throws java.io.IOException { return 0; }
    public static Path setLastModifiedTime(Path path, java.nio.file.attribute.FileTime time) throws java.io.IOException { return null; }
}
