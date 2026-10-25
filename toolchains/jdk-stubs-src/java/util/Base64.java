package java.util;
public class Base64 {
    public static Encoder getEncoder() { return null; }
    public static Encoder getUrlEncoder() { return null; }
    public static Encoder getMimeEncoder() { return null; }
    public static Encoder getMimeEncoder(int lineLength, byte[] lineSeparator) { return null; }
    public static Decoder getDecoder() { return null; }
    public static Decoder getUrlDecoder() { return null; }
    public static Decoder getMimeDecoder() { return null; }
    public static class Encoder {
        public byte[] encode(byte[] src) { return null; }
        public String encodeToString(byte[] src) { return null; }
        public int encode(byte[] src, byte[] dst) { return 0; }
        public java.nio.ByteBuffer encode(java.nio.ByteBuffer buffer) { return null; }
        public Encoder withoutPadding() { return null; }
    }
    public static class Decoder {
        public byte[] decode(byte[] src) { return null; }
        public byte[] decode(String src) { return null; }
        public java.nio.ByteBuffer decode(java.nio.ByteBuffer buffer) { return null; }
    }
}
