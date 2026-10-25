package java.util.concurrent;
public interface ConcurrentMap<K, V> extends java.util.Map<K, V> {
    V putIfAbsent(K key, V value);
    boolean remove(Object key, Object value);
    boolean replace(K key, V oldValue, V newValue);
    V replace(K key, V value);
}
