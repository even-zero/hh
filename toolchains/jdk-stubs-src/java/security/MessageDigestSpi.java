package java.security;
public abstract class MessageDigestSpi {
    protected int engineGetDigestLength() { return 0; }
    protected void engineUpdate(byte input) {}
    protected void engineUpdate(byte[] input, int offset, int len) {}
    protected void engineReset() {}
    protected byte[] engineDigest() { return null; }
    protected int engineDigest(byte[] buf, int offset, int len) throws DigestException { return 0; }
}
