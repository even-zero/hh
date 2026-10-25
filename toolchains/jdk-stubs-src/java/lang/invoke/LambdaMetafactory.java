package java.lang.invoke;
public final class LambdaMetafactory {
    private LambdaMetafactory() {}
    public static CallSite metafactory(MethodHandles.Lookup caller, String invokedName, MethodType invokedType,
            MethodType samMethodType, MethodHandle implMethod, MethodType instantiatedMethodType) throws LambdaConversionException { return null; }
    public static CallSite altMetafactory(MethodHandles.Lookup caller, String invokedName, MethodType invokedType,
            Object... args) throws LambdaConversionException { return null; }
}
