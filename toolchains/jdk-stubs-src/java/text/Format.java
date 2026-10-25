package java.text;
public abstract class Format implements Cloneable, java.io.Serializable {
    public abstract StringBuffer format(Object obj, StringBuffer toAppendTo, FieldPosition pos);
    public String format(Object obj) { return ""; }
    public Object parseObject(String source, ParsePosition pos) { return null; }
    public Object parseObject(String source) throws ParseException { return null; }
    public static class Field extends AttributedCharacterIterator.Attribute {
        protected Field(String name) { super(name); }
    }
    public Object clone() { try { return super.clone(); } catch (Exception e) { return null; } }
}
