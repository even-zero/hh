package java.text;
import java.util.Date; import java.util.Locale;
public class SimpleDateFormat extends DateFormat {
    public SimpleDateFormat() {}
    public SimpleDateFormat(String pattern) {}
    public SimpleDateFormat(String pattern, Locale locale) {}
    public StringBuffer format(Date date, StringBuffer toAppendTo, FieldPosition pos) { return toAppendTo; }
    public Date parse(String source, ParsePosition pos) { return null; }
    public String toPattern() { return ""; }
    public StringBuffer format(Object obj, StringBuffer toAppendTo, FieldPosition pos) { return toAppendTo; }
}
