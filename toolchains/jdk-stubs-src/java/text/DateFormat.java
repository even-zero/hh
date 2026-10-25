package java.text;
import java.util.Date; import java.util.Locale; import java.util.TimeZone;
public abstract class DateFormat extends Format {
    public static class Field extends Format.Field {
        protected Field(String name) { super(name); }
        public static final Field HOUR0 = new Field("hour0");
        public static final Field HOUR1 = new Field("hour1");
        public static final Field HOUR_OF_DAY0 = new Field("hour of day0");
    }
    public abstract StringBuffer format(Date date, StringBuffer toAppendTo, FieldPosition pos);
    public final String format(Date date) { return ""; }
    public Date parse(String source) throws ParseException { return null; }
    public abstract Date parse(String source, ParsePosition pos);
    public static final DateFormat getDateInstance() { return null; }
    public static final DateFormat getDateInstance(int style) { return null; }
    public static final DateFormat getDateTimeInstance() { return null; }
    public static final DateFormat getDateTimeInstance(int dateStyle, int timeStyle) { return null; }
    public static final DateFormat getTimeInstance() { return null; }
    public void setTimeZone(TimeZone tz) {}
    public TimeZone getTimeZone() { return null; }
    public void setLenient(boolean lenient) {}
    public void setLocale(Locale locale) {}
}
