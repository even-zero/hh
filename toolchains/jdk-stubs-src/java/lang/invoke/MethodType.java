package java.lang.invoke;
public final class MethodType {
    public static MethodType methodType(Class<?> rtype) { return null; }
    public static MethodType methodType(Class<?> rtype, Class<?> ptype0) { return null; }
    public static MethodType methodType(Class<?> rtype, Class<?>[] ptypes) { return null; }
    public static MethodType methodType(Class<?> rtype, MethodType ptypes) { return null; }
    public static MethodType genericMethodType(int objectArgCount) { return null; }
    public static MethodType genericMethodType(int objectArgCount, boolean trailingArray) { return null; }
    public Class<?> returnType() { return null; }
    public Class<?>[] parameterArray() { return null; }
    public int parameterCount() { return 0; }
    public MethodType changeReturnType(Class<?> nrtype) { return null; }
    public MethodType changeParameterType(int num, Class<?> nptype) { return null; }
    public MethodType appendParameterTypes(Class<?>... ptypesToInsert) { return null; }
}
