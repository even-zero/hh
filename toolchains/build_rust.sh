#!/bin/bash
# Build the Rust toolchain + cargo-nextest for the benchmark sandbox.
# Sources: AeEn123/rust-x86_64-unknown-linux-gnu (official dist tarball split
# into git contents) and pexip/os-cargo-nextest (nextest source + vendored deps).
set -x
T=/home/user/toolchains
mkdir -p "$T/rust-dist" "$T/bin"
cd "$T/rust-dist" || exit 1

echo "== 1. rust dist tree"
python3 - <<'EOF'
import urllib.request
url = "https://codeload.github.com/AeEn123/rust-x86_64-unknown-linux-gnu/tar.gz/refs/heads/main"
urllib.request.urlretrieve(url, "/home/user/toolchains/rust-dist/rustdist-tree.tar.gz")
print("tree downloaded")
EOF
tar xzf rustdist-tree.tar.gz
DIR=$(ls -d rust-x86_64-* | head -1)
cd "$DIR" || exit 1
ls

echo "== 2. assemble + extract"
cat xaa.tar.xz.part xab.tar.xz.part xac.tar.xz.part xad.tar.xz.part \
    xae.tar.xz.part xaf.tar.xz.part xag.tar.xz.part xah.tar.xz.part \
    > "$T/rust-dist/rust-full.tar.xz"
python3 - <<'EOF'
import tarfile
t = tarfile.open("/home/user/toolchains/rust-dist/rust-full.tar.xz", "r:xz")
t.extractall("/home/user/toolchains/rust-dist/")
print("extracted", len(t.getmembers()), "members")
EOF

echo "== 3. install rust"
cd "$T/rust-dist"/rust-1.94.1-x86_64-unknown-linux-gnu || ls "$T/rust-dist"
cd "$T/rust-dist"/rust-*-x86_64-unknown-linux-gnu || exit 1
./install.sh --prefix="$T/rust" --without=rust-docs,rust-docs-json 2>&1 | tail -6
"$T/rust/bin/rustc" --version || exit 1
"$T/rust/bin/cargo" --version || exit 1

echo "== 4. nextest source (vendored)"
cd "$T" || exit 1
python3 - <<'EOF'
import urllib.request
url = "https://codeload.github.com/pexip/os-cargo-nextest/tar.gz/refs/heads/HEAD"
try:
    urllib.request.urlretrieve(url, "/home/user/toolchains/nextest-src.tar.gz")
except Exception:
    urllib.request.urlretrieve(
        "https://codeload.github.com/pexip/os-cargo-nextest/tar.gz/refs/heads/master",
        "/home/user/toolchains/nextest-src.tar.gz")
print("nextest source downloaded")
EOF
rm -rf "$T/nextest-src"
mkdir -p "$T/nextest-src"
tar xzf nextest-src.tar.gz -C "$T/nextest-src" --strip-components=1
ls "$T/nextest-src" | head

echo "== 5. build cargo-nextest (offline, vendored)"
export PATH="$T/rust/bin:$PATH"
export CARGO_NET_OFFLINE=true
cd "$T/nextest-src" || exit 1
cargo build --release --locked -p cargo-nextest 2>&1 | tail -15
cp target/release/cargo-nextest "$T/bin/cargo-nextest" || exit 1
"$T/bin/cargo-nextest" --version || exit 1
"$T/bin/cargo-nextest" nextest --version || exit 1
echo "== RUST PIPELINE DONE"
