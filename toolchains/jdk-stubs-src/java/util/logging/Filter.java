package java.util.logging;
public interface Filter {
    boolean isLoggable(LogRecord record);
    default boolean isLoggable(Level level) { return true; }
}
