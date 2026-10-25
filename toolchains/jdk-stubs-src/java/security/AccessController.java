package java.security;
public final class AccessController {
    private AccessController() {}
    public static <T> T doPrivileged(PrivilegedAction<T> action) { return null; }
    public static <T> T doPrivileged(PrivilegedAction<T> action, AccessControlContext context) { return null; }
    public static <T> T doPrivileged(PrivilegedExceptionAction<T> action) throws PrivilegedActionException { return null; }
    public static <T> T doPrivileged(PrivilegedExceptionAction<T> action, AccessControlContext context) throws PrivilegedActionException { return null; }
    public static void checkPermission(Object perm) throws SecurityException {}
}
