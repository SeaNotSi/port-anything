#!/data/data/com.termux/files/usr/bin/sh
# Termux setup for Port Anything.
#
# Installs the optional toolchains and reports what is present. There is no
# root, no systemd and no Docker here: everything installs into $PREFIX and
# every cache lives under $TMPDIR or $HOME. Safe to run repeatedly.
#
# Usage: scripts/termux/setup.sh [--dry-run] [--core-only]

set -eu

DRY_RUN=0
CORE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --core-only) CORE_ONLY=1 ;;
    -h|--help)
      echo "usage: setup.sh [--dry-run] [--core-only]"
      exit 0
      ;;
    *)
      echo "setup.sh: unknown option $arg" >&2
      exit 2
      ;;
  esac
done

: "${PREFIX:=/data/data/com.termux/files/usr}"
if [ ! -d "$PREFIX" ]; then
  echo "setup.sh: PREFIX ($PREFIX) does not exist; this is not a Termux environment" >&2
  exit 2
fi

if ! command -v pkg >/dev/null 2>&1; then
  echo "setup.sh: 'pkg' not found; install the Termux app package manager first" >&2
  exit 2
fi

# Core: needed for the Python skill, the Rust native crate and the C++ probe.
CORE_PKGS="python rust clang cmake ninja git binutils"
# Optional: needed for the Android packaging path.
ANDROID_PKGS="openjdk-17 aapt2 d8 apksigner zipalign"

has_tool() {
  command -v "$1" >/dev/null 2>&1
}

need_pkg() {
  case "$1" in
    python)    has_tool python3 ;;
    rust)      has_tool cargo ;;
    clang)     has_tool clang++ ;;
    cmake)     has_tool cmake ;;
    ninja)     has_tool ninja ;;
    git)       has_tool git ;;
    binutils)  has_tool ld ;;
    openjdk-17) has_tool javac ;;
    aapt2)     has_tool aapt2 ;;
    d8)        has_tool d8 ;;
    apksigner) has_tool apksigner ;;
    zipalign)  has_tool zipalign ;;
    *) return 1 ;;
  esac
}

install_one() {
  pkg_name="$1"
  if need_pkg "$pkg_name"; then
    printf 'present  %s\n' "$pkg_name"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'would install  %s\n' "$pkg_name"
    return 0
  fi
  printf 'installing %s\n' "$pkg_name"
  pkg install -y "$pkg_name"
}

echo "termux setup — prefix=$PREFIX tmpdir=${TMPDIR:-<unset>}"
echo "core packages:"
for package in $CORE_PKGS; do
  install_one "$package"
done

if [ "$CORE_ONLY" -eq 0 ]; then
  echo "android packages (optional):"
  for package in $ANDROID_PKGS; do
    install_one "$package"
  done
fi

echo
echo "next:"
echo "  ./bin/pa doctor"
echo "  ./bin/pa apk env"
echo "  scripts/termux/build-native.sh"
