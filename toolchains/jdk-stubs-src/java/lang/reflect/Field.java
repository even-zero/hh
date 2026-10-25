package java.lang.reflect;
public final class Field extends AccessibleObject implements Member {
    public Class<?> getDeclaringClass() { return null; }
    public String getName() { return null; }
    public int getModifiers() { return 0; }
    public boolean isSynthetic() { return false; }
    public Class<?> getType() { return null; }
    public Type getGenericType() { return null; }
    public Object get(Object obj) throws IllegalArgumentException, IllegalAccessException { return null; }
    public void set(Object obj, Object value) throws IllegalArgumentException, IllegalAccessException {}
    public boolean getBoolean(Object obj) throws IllegalArgumentException, IllegalAccessException { return false; }
    public byte getByte(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public char getChar(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public short getShort(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public int getInt(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public long getLong(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public float getFloat(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public double getDouble(Object obj) throws IllegalArgumentException, IllegalAccessException { return 0; }
    public <T extends java.lang.annotation.Annotation> T getAnnotation(Class<T> annotationClass) { return null; }
    public java.lang.annotation.Annotation[] getAnnotations() { return null; }
    public java.lang.annotation.Annotation[] getDeclaredAnnotations() { return null; }
    public boolean isEnumConstant() { return false; }
}
