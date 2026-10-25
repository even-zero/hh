package java.security;
public class SecureRandom extends java.util.Random {
    public SecureRandom() {}
    public SecureRandom(byte[] seed) {}
    public void setSeed(byte[] seed) {}
    public void nextBytes(byte[] bytes) {}
    public int nextInt() { return 0; }
    public long nextLong() { return 0; }
    public boolean nextBoolean() { return false; }
    public float nextFloat() { return 0; }
    public double nextDouble() { return 0; }
    public static SecureRandom getInstance(String algorithm) throws NoSuchAlgorithmException { return null; }
    public static SecureRandom getInstance(String algorithm, String provider) throws NoSuchAlgorithmException, java.security.NoSuchProviderException { return null; }
    public String getAlgorithm() { return null; }
}
