package java.lang.reflect;
public class AccessibleObject implements AnnotatedElement {
    public void setAccessible(boolean flag) throws SecurityException {}
    public boolean isAccessible() { return false; }
    public static void setAccessible(AccessibleObject[] array, boolean flag) throws SecurityException {}
    public boolean isAnnotationPresent(Class<? extends java.lang.annotation.Annotation> annotationClass) { return false; }
    public <T extends java.lang.annotation.Annotation> T getAnnotation(Class<T> annotationClass) { return null; }
    public java.lang.annotation.Annotation[] getAnnotations() { return null; }
    public java.lang.annotation.Annotation[] getDeclaredAnnotations() { return null; }
}
