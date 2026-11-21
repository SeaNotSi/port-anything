#!/data/data/com.termux/files/usr/bin/sh
# Build the two native components and run their test suites.
#
#   Rust runtime   modules/ram-efficiency/native/pa-runtime   (cargo test)
#   C++ probe      modules/reverse-engineering/native/pa-probe (cmake + ctest)
#
# Everything is built under $TMPDIR when it is set, so no build output lands in
# the repository. Usage: scripts/termux/build-native.sh [--offline]

set -eu

SELF=$0
case $SELF in
  */*) DIR=$(cd "$(dirname "$SELF")" && pwd) ;;
  *)   DIR=$(pwd) ;;
esac
SKILL=$(cd "$DIR/.." && pwd)

OFFLINE=0
for arg in "$@"; do
  case "$arg" in
    --offline) OFFLINE=1 ;;
    -h|--help) echo "usage: build-native.sh [--offline]"; exit 0 ;;
    *) echo "build-native.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

WORK="${TMPDIR:-${HOME:-/tmp}}/pa-native-build"
mkdir -p "$WORK"

echo "== Rust runtime =="
RUST_DIR="$SKILL/modules/ram-efficiency/native/pa-runtime"
if command -v cargo >/dev/null 2>&1; then
  if [ "$OFFLINE" -eq 1 ]; then
    (cd "$RUST_DIR" && cargo test --offline)
    (cd "$RUST_DIR" && cargo build --release --offline)
  else
    (cd "$RUST_DIR" && cargo test)
    (cd "$RUST_DIR" && cargo build --release)
  fi
  echo "binary: $RUST_DIR/target/release/pa-runtime"
else
  echo "cargo MISSING — skipping the Rust runtime (scripts/termux/setup.sh)" >&2
fi

echo
echo "== C++ probe =="
CPP_DIR="$SKILL/modules/reverse-engineering/native/pa-probe"
if command -v cmake >/dev/null 2>&1; then
  BUILD="$WORK/pa-probe"
  cmake -S "$CPP_DIR" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$BUILD"
  ctest --test-dir "$BUILD" --output-on-failure
  echo "binary: $BUILD/pa-probe"
else
  echo "cmake MISSING — skipping the C++ probe (scripts/termux/setup.sh)" >&2
fi
