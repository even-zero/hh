package java.time.temporal;
public interface TemporalUnit {
    long getDuration(); boolean isDurationEstimated();
    boolean isSupportedBy(Temporal temporal);
    <R extends Temporal> R addTo(R temporal, long amount);
}
