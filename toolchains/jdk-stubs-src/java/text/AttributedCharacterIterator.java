package java.text;
import java.util.Map; import java.util.Set;
public interface AttributedCharacterIterator extends CharacterIterator {
    static class Attribute {
        protected Attribute(String name) {}
    }
    int getRunStart(); int getRunStart(Attribute attribute);
    int getRunLimit(); int getRunLimit(Attribute attribute);
    Map<Attribute, Object> getAttributes();
    Object getAttribute(Attribute attribute);
    Set<Attribute> getAllAttributeKeys();
}
