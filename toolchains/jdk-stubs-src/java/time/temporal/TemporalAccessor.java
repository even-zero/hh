package java.time.temporal;
public interface TemporalAccessor {
    boolean isSupported(TemporalField field);
    long getLong(TemporalField field);
}
