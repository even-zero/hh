package java.lang.reflect;
public final class Modifier {
    private Modifier() {}
    public static boolean isPublic(int mod) { return false; }
    public static boolean isPrivate(int mod) { return false; }
    public static boolean isProtected(int mod) { return false; }
    public static boolean isStatic(int mod) { return false; }
    public static boolean isFinal(int mod) { return false; }
    public static boolean isAbstract(int mod) { return false; }
    public static boolean isInterface(int mod) { return false; }
    public static String toString(int mod) { return null; }
}
