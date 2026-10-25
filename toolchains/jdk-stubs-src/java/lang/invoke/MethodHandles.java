package java.lang.invoke;
public final class MethodHandles {
    private MethodHandles() {}
    public static Lookup publicLookup() { return null; }
    public static Lookup lookup() { return null; }
    public static MethodHandle identity(Class<?> type) { return null; }
    public static MethodHandle zero(Class<?> type) { return null; }
    public static MethodHandle constant(Class<?> type, Object value) { return null; }
    public static MethodHandle filterReturnValue(MethodHandle target, MethodHandle filter) { return null; }
    public static MethodHandle catchException(MethodHandle target, Class<? extends Throwable> exType, MethodHandle handler) { return null; }
    public static MethodHandle invoker(MethodType type) { return null; }

    public static final class Lookup {
        public Class<?> lookupClass() { return null; }
        public MethodHandle findStatic(Class<?> refc, String name, MethodType type) throws NoSuchMethodException, IllegalAccessException { return null; }
        public MethodHandle findVirtual(Class<?> refc, String name, MethodType type) throws NoSuchMethodException, IllegalAccessException { return null; }
        public MethodHandle findSpecial(Class<?> refc, String name, MethodType type, Class<?> specialCaller) throws NoSuchMethodException, IllegalAccessException { return null; }
        public MethodHandle findConstructor(Class<?> refc, MethodType type) throws NoSuchMethodException, IllegalAccessException { return null; }
        public MethodHandle unreflect(java.lang.reflect.Method m) throws IllegalAccessException { return null; }
        public MethodHandle unreflectConstructor(java.lang.reflect.Constructor<?> c) throws IllegalAccessException { return null; }
        public MethodHandle unreflectGetter(java.lang.reflect.Field f) throws IllegalAccessException { return null; }
        public MethodHandle unreflectSetter(java.lang.reflect.Field f) throws IllegalAccessException { return null; }
        public MethodHandle findGetter(Class<?> refc, String name, Class<?> type) throws NoSuchFieldException, IllegalAccessException { return null; }
        public MethodHandle findSetter(Class<?> refc, String name, Class<?> type) throws NoSuchFieldException, IllegalAccessException { return null; }
        public MethodHandle findStaticGetter(Class<?> refc, String name, Class<?> type) throws NoSuchFieldException, IllegalAccessException { return null; }
        public MethodHandle findStaticSetter(Class<?> refc, String name, Class<?> type) throws NoSuchFieldException, IllegalAccessException { return null; }
    }
}
