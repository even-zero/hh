package java.text;
public abstract class NumberFormat extends Format {
    public static class Field extends Format.Field {
        protected Field(String name) { super(name); }
        public static final Field INTEGER = new Field("integer");
        public static final Field FRACTION = new Field("fraction");
    }
    public abstract StringBuffer format(Object number, StringBuffer toAppendTo, FieldPosition pos);
    public final String format(long number) { return ""; }
    public final String format(double number) { return ""; }
    public abstract Number parse(String source, ParsePosition pos);
    public Number parse(String source) throws ParseException { return null; }
    public static final NumberFormat getInstance() { return null; }
    public static NumberFormat getInstance(java.util.Locale locale) { return null; }
    public static NumberFormat getNumberInstance(java.util.Locale locale) { return null; }
    public static final NumberFormat getNumberInstance() { return null; }
    public static final NumberFormat getIntegerInstance() { return null; }
    public void setMaximumIntegerDigits(int n) {}
    public void setMinimumIntegerDigits(int n) {}
}
