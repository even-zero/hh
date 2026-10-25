#!/bin/bash
# Build the JUnit stack (hamcrest, junit4, junit-platform, jupiter-api,
# vintage-engine, apiguardian, opentest4j) with the checker-javac wrapper.
# Sources: pinned GitHub tags fetched via codeload.
set -x
T=/home/user/toolchains
SRC="$T/src"
CLS="$T/junit-classes"
JAVAC="$T/bin/javac"
J="$T/java-compiler"
mkdir -p "$SRC" "$CLS"
export PATH="$T/bin:$PATH"

fetch() { # name url
  if [ ! -d "$SRC/$1" ]; then
    python3 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$2" "$SRC/$1.tar.gz" || return 1
    mkdir -p "$SRC/$1"
    tar xzf "$SRC/$1.tar.gz" -C "$SRC/$1" --strip-components=1 || return 1
  fi
}

echo "== fetch sources"
fetch apiguardian "https://codeload.github.com/apiguardian-team/apiguardian/tar.gz/refs/tags/r1.1.2" || exit 1
fetch opentest4j "https://codeload.github.com/ota4j-team/opentest4j/tar.gz/refs/tags/r1.3.0" || exit 1
fetch hamcrest "https://codeload.github.com/hamcrest/JavaHamcrest/tar.gz/refs/tags/v2.2" || exit 1
fetch junit4 "https://codeload.github.com/junit-team/junit4/tar.gz/refs/tags/r4.13.2" || exit 1
fetch junit5 "https://codeload.github.com/junit-team/junit5/tar.gz/refs/tags/r5.10.2" || exit 1
ls "$SRC"

J5="$SRC/junit5"
echo "junit5 root: $J5"
ls "$J5" | head -30

compile() { # outdir, classpath, then file list on stdin via args...
  out="$1"; cp="$2"; shift 2
  mkdir -p "$CLS/$out"
  "$JAVAC" -cp "$cp" -d "$CLS/$out" "$@" || return 1
}

echo "== 1. apiguardian"
A="$CLS/apiguardian"; mkdir -p "$A"
"$JAVAC" -d "$A" $(find "$SRC/apiguardian" -path '*src/main/java*' -name '*.java') || exit 1

echo "== 2. opentest4j"
O="$CLS/opentest4j"; mkdir -p "$O"
"$JAVAC" -cp "$A" -d "$O" $(find "$SRC/opentest4j" -path '*src/main/java*' -name '*.java') || exit 1

echo "== 3. hamcrest"
H="$CLS/hamcrest"; mkdir -p "$H"
HFILES=$(find "$SRC/hamcrest/hamcrest/src/main/java" -name '*.java' \
  | grep -v -e '/org/hamcrest/xml/' -e '/org/hamcrest/beans/' -e 'org/hamcrest/Matchers.java' -e '/hamcrest-extra/')
HFILES="$HFILES $T/hamcrest-extra/Factory.java"
echo "$HFILES" | wc -l
"$JAVAC" -d "$H" $HFILES || exit 1

echo "== 4. junit4 main"
sed -i 's|return CoreMatchers.everyItem(elementMatcher);|return (Matcher) CoreMatchers.everyItem(elementMatcher);|' "$SRC/junit4/src/main/java/org/junit/matchers/JUnitMatchers.java"
M="$CLS/junit4"; mkdir -p "$M"
"$JAVAC" -cp "$H:$A:$O" -d "$M" $(find "$SRC/junit4/src/main/java" -name '*.java') || exit 1

echo "== 5. junit-platform-commons"
PC="$CLS/junit-platform-commons"; mkdir -p "$PC"
"$JAVAC" -cp "$O:$A" -d "$PC" $(find "$J5/junit-platform-commons/src/main/java" -name '*.java') || exit 1

echo "== 6. junit-platform-engine"
PE="$CLS/junit-platform-engine"; mkdir -p "$PE"
"$JAVAC" -cp "$PC:$O:$A" -d "$PE" $(find "$J5/junit-platform-engine/src/main/java" -name '*.java') || exit 1

echo "== 7. junit-platform-suite-api"
PS="$CLS/junit-platform-suite-api"; mkdir -p "$PS"
"$JAVAC" -cp "$PC:$PE:$O:$A" -d "$PS" $(find "$J5/junit-platform-suite-api/src/main/java" -name '*.java') || exit 1

echo "== 8. junit-platform-launcher"
PL="$CLS/junit-platform-launcher"; mkdir -p "$PL"
"$JAVAC" -cp "$PC:$PE:$PS:$O:$A" -d "$PL" $(find "$J5/junit-platform-launcher/src/main/java" -name '*.java') || exit 1

echo "== 9. junit-jupiter-api"
JA="$CLS/junit-jupiter-api"; mkdir -p "$JA"
"$JAVAC" -cp "$PC:$O:$A" -d "$JA" $(find "$J5/junit-jupiter-api/src/main/java" -name '*.java') || exit 1

echo "== 10. junit-vintage-engine"
VE="$CLS/junit-vintage-engine"; mkdir -p "$VE"
"$JAVAC" -cp "$PC:$PE:$M:$H:$O:$A" -d "$VE" $(find "$J5/junit-vintage-engine/src/main/java" -name '*.java') || exit 1

echo "== classpath file"
echo -n "$H:$A:$O:$M:$PC:$PE:$PS:$PL:$JA:$VE" > "$T/junit-classpath.txt"
cat "$T/junit-classpath.txt"; echo

echo "== smoke: compile HhJunit driver"
mkdir -p "$CLS/hhdriver"
"$JAVAC" -cp "$(cat "$T/junit-classpath.txt")" -d "$CLS/hhdriver" /home/user/hh/executor/adapters/HhJunit.java || exit 1

echo "== JAVA STACK DONE"
