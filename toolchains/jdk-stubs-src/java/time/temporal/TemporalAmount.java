package java.time.temporal;
public interface TemporalAmount {
    long get(TemporalUnit unit);
    java.util.List<TemporalUnit> getUnits();
}
