package java.time.temporal;
public interface TemporalField {
    String getDisplayName(java.util.Locale locale);
    TemporalUnit getBaseUnit();
    boolean isSupportedBy(TemporalAccessor temporal);
}
