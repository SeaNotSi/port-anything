# Termux notes

Port Anything treats Termux as a first-class host, not a degraded one. The rules
below are what "first-class" means in practice.

## Shell facts the code relies on

- `$PREFIX` is the install root (`/data/data/com.termux/files/usr`). Never
  assume `/usr`, `/bin`, `/opt` or a `/usr/bin/env`.
- `$HOME` is `/data/data/com.termux/files/home`.
- `$TMPDIR` is `/data/data/com.termux/files/usr/tmp` when set. Build output and
  throwaway artifacts go here, not into the repo and not into `/tmp`.
- The launcher `bin/pa` and `scripts/pa.py` are POSIX `sh`/Python and resolve
  the skill root relative to themselves, so they work wherever the tree lives.

## No root, no systemd, no Docker

Nothing here installs a daemon, writes outside the project or `$TMPDIR`, or
needs a container. Long jobs are ordinary foreground processes managed by the
RAM policy; there is no service unit to enable.

## What Termux does and does not ship

Measured on the reference host (Android 14 / API 34, aarch64):

| Present | Missing / broken |
|---|---|
| `rustc`, `cargo` | Android SDK (`android.jar` absent) |
| `clang`, `clang++`, `cmake`, `ninja`, `ld` | Android NDK |
| `python3`, `git`, `binutils` | `zipalign` |
| OpenJDK (`java`, `javac`, `keytool`) | `gradle` |
| `aapt2`, `d8`, `apksigner` | `adb` (present but cannot link) |

Consequences, and the framework reports exactly these:

- Resource linking of a manifest that uses `android:` attributes is
  `unsupported` without a framework jar. A manifest with no `android:`
  attributes still links (this is what `pa apk selftest` uses).
- 4-byte alignment is not verified without `zipalign`.
- Device tests are `unsupported`: the `adb` binary fails at the loader, and the
  loader error is attached to the stage rather than reported as "no device".

## Reconciling packages

`scripts/termux/setup.sh` installs the core toolchain (`python`, `rust`,
`clang`, `cmake`, `ninja`, `git`, `binutils`) and, unless `--core-only` is
passed, the optional Android tools (`openjdk-17`, `aapt2`, `d8`, `apksigner`,
`zipalign`). It checks tool presence before calling `pkg install`, so re-running
is safe.

## Building the native components

`scripts/termux/build-native.sh` builds and tests both native components under
`$TMPDIR`:

- the Rust runtime (`cargo test`, `cargo build --release`),
- the C++ probe (`cmake` + `ctest`).

Pass `--offline` when there is no network; the Rust crate has no dependencies,
so it builds offline.

## Where state lives

- Skill cache and run state: `$XDG_CACHE_HOME/port-anything` or
  `$HOME/.cache/port-anything` (override with `PA_CACHE_DIR` / `PA_STATE_DIR`).
- Dataset index: `datasets/index/pakb.sqlite` inside the skill tree.
- Build output: under `$TMPDIR` for tests, under the project for real builds.
