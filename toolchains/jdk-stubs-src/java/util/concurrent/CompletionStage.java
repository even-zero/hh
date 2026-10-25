package java.util.concurrent;
public interface CompletionStage<T> {
    <U> CompletionStage<U> thenApply(java.util.function.Function<? super T, ? extends U> fn);
    CompletionStage<Void> thenRun(Runnable action);
    CompletionStage<Void> thenAccept(java.util.function.Consumer<? super T> action);
    <U> CompletionStage<U> handle(java.util.function.BiFunction<? super T, Throwable, ? extends U> fn);
    CompletableFuture<T> toCompletableFuture();
}
