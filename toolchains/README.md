# toolchains/ — sandbox toolchain build recipes (reset insurance)

Everything under this directory is *source* used to rebuild the benchmark
toolchains in `/home/user/toolchains` after a sandbox reset. Rebuildable
binaries (rustc, cargo-nextest, jars, class trees) are NOT committed.

Rebuild order after a reset:

1. `bash toolchains/build_rust.sh` — Rust 1.94.1 from
   AeEn123/rust-x86_64-unknown-linux-gnu (git contents mirror of
   static.rust-lang.org) + cargo-nextest 0.9.x from pexip/os-cargo-nextest
   (Debian-packaged source with committed `vendor/`, builds fully offline).
2. Java: jars come from JMRI/JMRI `lib/checker-framework/checker/dist/`:
   `javac.jar` (the Checker Framework's javac, run via
   `java -cp javac.jar com.sun.tools.javac.Main`) and `jdk8.jar` (annotated
   JDK-8 API stubs). `jdk-stubs-src/` + `mk_rtjar.sh` compile the missing
   JDK API pieces and merge them over jdk8.jar into `java-compiler/rt.jar`,
   which the `bin/javac` wrapper uses as `-bootclasspath -source 8 -target 8`
   (stubs are compile-time only; tests run on the real jdk4py JVM).
3. `bash toolchains/build_java_stack.sh` — hamcrest v2.2 (+ `hamcrest-extra/Factory.java`),
   junit4 r4.13.2, junit5 r5.10.2 platform/jupiter-api/vintage modules →
   `junit-classes/` + `junit-classpath.txt` (HH_JUNIT_CLASSPATH).
4. PHP/Ruby scripts (see build_phpunit.py / build_ruby.sh counterparts)
   rebuild the phpunit 9.6.29 and rspec stacks.

`python3 -m benchmark` must run with cwd=/home/user/hh; PATH must include
/home/user/toolchains/bin and /home/user/toolchains/rust/bin.
