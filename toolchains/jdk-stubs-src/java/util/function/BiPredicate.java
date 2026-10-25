package java.util.function;
public interface BiPredicate<T, U> {
    boolean test(T t, U u);
    default BiPredicate<T, U> and(BiPredicate<? super T, ? super U> other) { return null; }
    default BiPredicate<T, U> or(BiPredicate<? super T, ? super U> other) { return null; }
    default BiPredicate<T, U> negate() { return null; }
}
