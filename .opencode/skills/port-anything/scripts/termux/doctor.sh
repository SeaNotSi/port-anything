#!/data/data/com.termux/files/usr/bin/sh
# Environment report for Port Anything on Termux.
#
# Prints the shell facts the skill depends on ($HOME, $PREFIX, $TMPDIR), the
# compiled/native toolchains and the Android packaging tools, then delegates to
# `pa doctor` and `pa apk env` for the machine-readable view.
#
# Usage: scripts/termux/doctor.sh [--json]

set -eu

SELF=$0
case $SELF in
  */*) DIR=$(cd "$(dirname "$SELF")" && pwd) ;;
  *)   DIR=$(pwd) ;;
esac
SKILL=$(cd "$DIR/../.." && pwd)
ROOT=$(cd "$SKILL/../../.." && pwd)

JSON=0
for arg in "$@"; do
  case "$arg" in
    --json) JSON=1 ;;
    -h|--help) echo "usage: doctor.sh [--json]"; exit 0 ;;
    *) echo "doctor.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

PA=""
if [ -x "$ROOT/bin/pa" ]; then
  PA="$ROOT/bin/pa"
elif [ -x "$ROOT/../bin/pa" ]; then
  PA="$ROOT/../bin/pa"
elif [ -x "$SKILL/scripts/pa.py" ]; then
  PA="python3 $SKILL/scripts/pa.py"
fi

if [ "$JSON" -eq 1 ]; then
  if [ -n "$PA" ]; then
    exec $PA doctor --json
  fi
  echo '{"error": "pa launcher not found"}' >&2
  exit 2
fi

echo "shell"
echo "  HOME   : ${HOME:-<unset>}"
echo "  PREFIX : ${PREFIX:-<unset>}"
echo "  TMPDIR : ${TMPDIR:-<unset>}"
echo "  shell  : ${SHELL:-<unset>}"
echo
echo "native toolchain"
for tool in cc c++ clang clang++ rustc cargo cmake ninja ld python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '  %-8s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '  %-8s %s\n' "$tool" "MISSING"
  fi
done
echo
echo "android toolchain"
for tool in java javac keytool aapt2 d8 apksigner zipalign gradle adb; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '  %-8s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '  %-8s %s\n' "$tool" "MISSING"
  fi
done
echo
if [ -n "$PA" ]; then
  $PA apk env
else
  echo "pa launcher not found; run scripts/termux/setup.sh" >&2
fi
