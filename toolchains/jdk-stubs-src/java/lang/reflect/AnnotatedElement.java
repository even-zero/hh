package java.lang.reflect;
public interface AnnotatedElement {
    boolean isAnnotationPresent(Class<? extends java.lang.annotation.Annotation> annotationClass);
    <T extends java.lang.annotation.Annotation> T getAnnotation(Class<T> annotationClass);
    java.lang.annotation.Annotation[] getAnnotations();
    java.lang.annotation.Annotation[] getDeclaredAnnotations();
    default <T extends java.lang.annotation.Annotation> T getDeclaredAnnotation(Class<T> annotationClass) { return null; }
    default <T extends java.lang.annotation.Annotation> T[] getAnnotationsByType(Class<T> annotationClass) { return null; }
}
