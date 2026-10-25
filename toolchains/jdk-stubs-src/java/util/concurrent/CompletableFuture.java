package java.util.concurrent;
public class CompletableFuture<T> implements Future<T>, CompletionStage<T> {
    public CompletableFuture() {}
    public static <U> CompletableFuture<U> completedFuture(U value) { return null; }
    public static CompletableFuture<Void> runAsync(Runnable runnable) { return null; }
    public static <U> CompletableFuture<U> supplyAsync(java.util.function.Supplier<U> supplier) { return null; }
    public boolean isDone() { return false; }
    public T get() throws InterruptedException, ExecutionException { return null; }
    public T get(long timeout, TimeUnit unit) throws InterruptedException, ExecutionException, TimeoutException { return null; }
    public T join() { return null; }
    public T getNow(T valueIfAbsent) { return null; }
    public boolean complete(T value) { return false; }
    public boolean completeExceptionally(Throwable ex) { return false; }
    public boolean cancel(boolean mayInterruptIfRunning) { return false; }
    public boolean isCancelled() { return false; }
    public boolean isCompletedExceptionally() { return false; }
    public CompletableFuture<T> whenComplete(java.util.function.BiConsumer<? super T, ? super Throwable> action) { return null; }
    public <U> CompletableFuture<U> thenApply(java.util.function.Function<? super T, ? extends U> fn) { return null; }
    public CompletableFuture<Void> thenRun(Runnable action) { return null; }
    public CompletableFuture<Void> thenAccept(java.util.function.Consumer<? super T> action) { return null; }
    public <U> CompletableFuture<U> thenCompose(java.util.function.Function<? super T, ? extends CompletionStage<U>> fn) { return null; }
    public <U> CompletableFuture<U> handle(java.util.function.BiFunction<? super T, Throwable, ? extends U> fn) { return null; }
    public CompletableFuture<T> toCompletableFuture() { return this; }
}
