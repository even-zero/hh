package java.lang.reflect;
public abstract class Executable extends AccessibleObject implements Member, GenericDeclaration {
    public Class<?> getDeclaringClass() { return null; }
    public String getName() { return null; }
    public int getModifiers() { return 0; }
    public boolean isSynthetic() { return false; }
    public TypeVariable<?>[] getTypeParameters() { return null; }
    public Class<?>[] getParameterTypes() { return null; }
    public Class<?>[] getExceptionTypes() { return null; }
    public String toGenericString() { return null; }
    public boolean isAnnotationPresent(Class<? extends java.lang.annotation.Annotation> annotationClass) { return false; }
    public <T extends java.lang.annotation.Annotation> T getAnnotation(Class<T> annotationClass) { return null; }
    public java.lang.annotation.Annotation[] getAnnotations() { return null; }
    public java.lang.annotation.Annotation[] getDeclaredAnnotations() { return null; }
    public int getParameterCount() { return 0; }
    public java.lang.annotation.Annotation[][] getParameterAnnotations() { return null; }
    public Parameter[] getParameters() { return null; }
}
