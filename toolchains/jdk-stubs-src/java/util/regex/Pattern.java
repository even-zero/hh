package java.util.regex;
public final class Pattern implements java.io.Serializable {
    public static final int CASE_INSENSITIVE = 0x02;
    public static final int MULTILINE = 0x08;
    public static final int DOTALL = 0x20;
    public static final int UNICODE_CASE = 0x40;
    public static final int UNICODE_CHARACTER_CLASS = 0x100;
    public static Pattern compile(String regex) { return null; }
    public static Pattern compile(String regex, int flags) { return null; }
    public static boolean matches(String regex, CharSequence input) { return false; }
    public static String quote(String s) { return null; }
    public Matcher matcher(CharSequence input) { return null; }
    public String[] split(CharSequence input) { return null; }
    public String pattern() { return null; }
}
