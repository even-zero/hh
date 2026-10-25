package java.security;
public abstract class Permission {
    public abstract boolean implies(Object permission);
}
