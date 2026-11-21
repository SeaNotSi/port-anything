# Environment Report — Port Anything

Captured during Phase 0 on the development host. Re-run
`scripts/termux/doctor.sh` (or `pa doctor`) on any machine to regenerate an
equivalent report.

## Host

| Property | Value | Source |
|---|---|---|
| Kernel | `Linux 5.10.237-android12-9-... aarch64 Android` | `uname -a` |
| Platform | Android 14 (SDK 34) | `getprop ro.build.version.sdk` |
| ABI | `arm64-v8a` | `getprop ro.product.cpu.abi` |
| Environment | Termux (non-root, no systemd, no Docker) | `$PREFIX` present |
| `$HOME` | `/data/data/com.termux/files/home` | env |
| `$PREFIX` | `/data/data/com.termux/files/usr` | env |
| `$TMPDIR` | `/data/data/com.termux/files/usr/tmp` | env |
| CPU cores | 8 | `nproc` |
| RAM total | 7685 MiB | `/proc/meminfo` |
| RAM available | ~1567 MiB (observed, fluctuates) | `/proc/meminfo` |
| Swap | 4209 MiB total, 3657 MiB used | `free -m` |
| Storage free | 39 GiB of 106 GiB | `df -h` |

## Toolchain

| Tool | Version | Status |
|---|---|---|
| rustc | 1.98.1 | present |
| cargo | 1.98.1 | present |
| rust-std `aarch64-linux-android` | 1.98.1 | installed (Termux package) |
| clang / clang++ | 21.1.8 | present |
| cmake | 4.4.5 | present |
| ninja | 1.13.2 | present |
| python3 | 3.14.10 | present |
| java | OpenJDK 21.0.12 | present |
| gradle | — | **missing** |
| git | 2.56.0 | present |
| node / npm | 24.22.1 / 11.19.0 | present |
| sqlite3 CLI | — | **missing** (Python `sqlite3` module works) |
| aapt2 | 16.0.0.4 | present (Termux package) |
| d8 | 37.0.0 | present (Termux package) |
| apksigner | 37.0.0 | present (Termux package) |
| apktool | 3.0.3 | present |
| ndk-sysroot | 29-2 | present (headers + libs, **not** a full NDK) |
| Android SDK | — | **missing** (`ANDROID_HOME` unset) |
| Android NDK | — | **missing** (`NDK_HOME` unset) |

## Python modules

| Module | Status |
|---|---|
| `yaml` | available |
| `sqlite3` | available |
| `tomllib`, `hashlib`, `zipfile`, `struct` | stdlib |
| `jsonschema` | **missing** → the project ships a dependency-free schema validator subset |

## OpenCode

| Property | Value |
|---|---|
| Version | `opencode v2.2.6` |
| Global config | `~/.config/opencode/opencode.json` (provider `tokenharbor`, many models) |
| Existing global skills | `~/.config/opencode/skills/{uncensored,worm}` |
| Existing skill library | `~/.agents/skills/*` (67 skills, read by OpenCode) |
| Existing agents dir | absent → created by this project's installer |
| Project config | none before this project |

## Capability conclusions

- **Local analysis, dataset indexing, Rust and C++ compilation, resource
  monitoring: fully supported on this host.**
- **Full Gradle-based APK packaging: NOT supported here** (no Android SDK/NDK,
  no Gradle). The pipeline detects this and refuses to claim an APK.
- **Fallback APK assembly: partially supported.** `aapt2` + `d8` + `apksigner`
  are present, which is enough to assemble and sign a minimal APK from
  prebuilt dex + native libs. The pipeline implements this path and validates
  the output, but a real game port still needs the NDK to produce
  `lib*.so` for `arm64-v8a`. `ndk-sysroot` alone is not sufficient for
  a full NDK build.
- **Root access: absent.** No kernel drivers, no privileged operations, no
  `systemd`, no Docker. Nothing in this project requires them.
- **Network: not required.** Rust crate is dependency-free; Python uses stdlib
  only; dataset is bundled and offline.

## Verified vs. unverified

| Claim | Status |
|---|---|
| Rust crate compiles and tests on this host | TESTED |
| C++ probe compiles with clang on this host | TESTED |
| Python test suite runs on this host | TESTED |
| Dataset validates and indexes to SQLite | TESTED |
| Termux detection scripts | TESTED on real Termux |
| Gradle APK build | UNSUPPORTED on this host (no SDK/NDK/Gradle) |
| aapt2/d8/apksigner fallback assembly | TESTED (minimal APK, see `tests/`) |
| Runtime smoke test on device | NOT TESTED (no game payload; requires user-supplied authorized build) |
| Desktop Linux / macOS | NOT TESTED |
