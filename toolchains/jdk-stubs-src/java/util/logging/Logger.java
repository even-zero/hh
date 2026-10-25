package java.util.logging;
public class Logger {
    public static final String GLOBAL_LOGGER_NAME = "global";
    public static Logger getGlobal() { return null; }
    public static Logger getLogger(String name) { return null; }
    public static Logger getLogger(String name, String resourceBundleName) { return null; }
    public void log(LogRecord record) {}
    public void log(Level level, String msg) {}
    public void log(Level level, String msg, Object param1) {}
    public void log(Level level, String msg, Throwable thrown) {}
    public void log(Level level, java.util.function.Supplier<String> msgSupplier) {}
    public void log(Level level, Throwable thrown, java.util.function.Supplier<String> msgSupplier) {}
    public void log(Level level, String msg, Object... params) {}
    public void severe(String msg) {}
    public void warning(String msg) {}
    public void info(String msg) {}
    public void fine(String msg) {}
    public Level getLevel() { return null; }
    public void setLevel(Level newLevel) throws SecurityException {}
    public String getName() { return null; }
    public void addHandler(Handler handler) throws SecurityException {}
    public void removeHandler(Handler handler) throws SecurityException {}
    public void setUseParentHandlers(boolean useParentHandlers) {}
    public boolean getUseParentHandlers() { return false; }
    public void setFilter(Filter newFilter) throws SecurityException {}
    public boolean isLoggable(Level level) { return false; }
    public java.util.ResourceBundle getResourceBundle() { return null; }
    public String getResourceBundleName() { return null; }
    public void setResourceBundle(java.util.ResourceBundle bundle) {}
    public Handler[] getHandlers() { return null; }
    public Logger getParent() { return null; }
    public void setParent(Logger parent) {}
}
