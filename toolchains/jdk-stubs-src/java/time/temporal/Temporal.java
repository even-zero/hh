package java.time.temporal;
public interface Temporal extends TemporalAccessor {
    Temporal plus(long amountToAdd, TemporalUnit unit);
    boolean isSupported(TemporalField field);
}
