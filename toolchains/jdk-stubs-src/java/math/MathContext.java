package java.math;
public final class MathContext implements java.io.Serializable {
    public static final MathContext UNLIMITED = null;
    public static final MathContext DECIMAL32 = null;
    public static final MathContext DECIMAL64 = null;
    public static final MathContext DECIMAL128 = null;
    public MathContext(int precision) {}
    public MathContext(int precision, RoundingMode roundingMode) {}
    public MathContext(int precision, int roundingMode) {}
    public int getPrecision() { return 0; }
    public RoundingMode getRoundingMode() { return null; }
}
