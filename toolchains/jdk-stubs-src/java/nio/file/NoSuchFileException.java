package java.nio.file;
public class NoSuchFileException extends java.nio.file.FileSystemException {
    public NoSuchFileException(String file) { super(file, null, null); }
}
