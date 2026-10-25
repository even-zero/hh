package java.lang.reflect;
public final class Parameter implements AnnotatedElement {
    public boolean isNamePresent() { return false; }
    public boolean isImplicit() { return false; }
    public String getName() { return null; }
    public Class<?> getType() { return null; }
    public Type getParameterizedType() { return null; }
    public int getModifiers() { return 0; }
    public Executable getDeclaringExecutable() { return null; }
    public boolean isAnnotationPresent(Class<? extends java.lang.annotation.Annotation> annotationClass) { return false; }
    public <T extends java.lang.annotation.Annotation> T getAnnotation(Class<T> annotationClass) { return null; }
    public java.lang.annotation.Annotation[] getAnnotations() { return null; }
    public java.lang.annotation.Annotation[] getDeclaredAnnotations() { return null; }
}
