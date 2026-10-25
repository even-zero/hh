package java.util.regex;
public final class Matcher implements MatchResult {
    public Matcher appendReplacement(StringBuffer sb, String replacement) { return this; }
    public StringBuffer appendTail(StringBuffer sb) { return null; }
    public boolean find() { return false; }
    public boolean find(int start) { return false; }
    public boolean matches() { return false; }
    public String group() { return null; }
    public String group(int group) { return null; }
    public int groupCount() { return 0; }
    public int start() { return 0; }
    public int start(int group) { return 0; }
    public int end() { return 0; }
    public int end(int group) { return 0; }
    public Pattern pattern() { return null; }
    public static String quoteReplacement(String s) { return null; }
    public String replaceFirst(String replacement) { return null; }
    public Matcher reset() { return this; }
    public String replaceAll(String replacement) { return null; }
}
