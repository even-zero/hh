package java.nio.file;
public class FileSystemException extends java.io.IOException {
    public FileSystemException(String file) { super(file); }
    public FileSystemException(String file, String other, String reason) { super(reason); }
    public String getFile() { return null; }
    public String getOtherFile() { return null; }
    public String getReason() { return null; }
}
