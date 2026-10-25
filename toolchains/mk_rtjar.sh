#!/bin/bash
set -e
T=/home/user/toolchains
rm -rf "$T/jdk-stubs-classes"; mkdir -p "$T/jdk-stubs-classes"
cd "$T/jdk-stubs-src"
/usr/local/lib/python3.11/dist-packages/jdk4py/java-runtime/bin/java -cp "$T/java-compiler/javac.jar" com.sun.tools.javac.Main -source 8 -target 8 -bootclasspath "$T/java-compiler/jdk8.jar" -d "$T/jdk-stubs-classes" $(find . -name '*.java')
python3 - <<'PY'
import zipfile, os
T='/home/user/toolchains'
out = zipfile.ZipFile(T+'/java-compiler/rt.jar', 'w', zipfile.ZIP_DEFLATED)
z = zipfile.ZipFile(T+'/java-compiler/jdk8.jar')
stubs=set()
for root,_,files in os.walk(T+'/jdk-stubs-classes'):
    for f in files:
        stubs.add(os.path.relpath(os.path.join(root,f), T+'/jdk-stubs-classes'))
for n in z.namelist():
    if n.endswith('/') or n in stubs: continue
    out.writestr(n, z.read(n))
count=0
for root,_,files in os.walk(T+'/jdk-stubs-classes'):
    for f in files:
        full=os.path.join(root,f)
        arc=os.path.relpath(full, T+'/jdk-stubs-classes')
        out.writestr(arc, open(full,'rb').read()); count+=1
out.close()
print('rt.jar rebuilt:', count, 'stubs')
PY
