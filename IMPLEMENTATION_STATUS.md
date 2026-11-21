# Implementation status

Updated: 2026-11-22

This file records what exists, what was measured, and what is still missing. A
phase is only marked done when its code runs and its tests pass on this host.

## Phase table

| Phase | Scope | State | Evidence |
|---|---|---|---|
| 0 | Environment inspection | done | `docs/ENVIRONMENT_REPORT.md`, `pa doctor` |
| 1 | Repository and OpenCode config | done | `opencode.jsonc`, `references/house-rules.md`, `SKILL.md`, module READMEs, references |
| 2 | PAKB dataset | done | 128 records, 10 categories, 5 schemas, 44 sources, SQLite index, `pa dataset validate` = 0 errors / 3 warnings |
| 3 | Reverse engineering | done | 15 analyzers, fingerprint, compatibility engine, report renderer; 21 tests |
| 4 | Token efficiency | done | estimation, source index, lossless planner, guard, dedupe, cache; 28 tests |
| 5 | RAM efficiency | done | measured monitor, policy, adaptive runner, artifact cache, `pa-runtime` crate (17+4 tests); 55 tests |
| 6 | Multi-agent orchestration | done | contract, DAG, run store, claim conflicts, scheduler, 9 agent files; 69 tests |
| 7 | APK build pipeline | done (honest limits) | 11 APK modules, per-stage states, claims block, `apk selftest` 7/7 DONE; 68 tests |
| 8 | Installer + Termux scripts | done | `pa install` 5 modes, 114 files, idempotent; `scripts/termux/*.sh`; 15 tests |
| 9 | Docs, GitHub files, CI | done | `README.md`, `LICENSE`, `CONTRIBUTING.md`, `.github/workflows/ci.yml`, PR + issue templates |

**Total: 256 tests pass** — 21 RE + 28 tokens + 55 RAM + 69 agents + 68 APK + 15
installer. Plus `cargo test --offline` (17 unit + 4 integration) and `ctest`
(3/3) on the native components.

Run from `port-anything/`:

```sh
python3 -m unittest discover -s .opencode/skills/port-anything/tests -p 'test_*.py'
```

## Phase 7 detail — APK build

Modules under `src/pa/apk/`:

| File | Job |
|---|---|
| `stages.py` | the five states and their constructors; `from_result` maps a process result to a state |
| `env.py` | tool discovery by *measured execution*; `broken` ≠ `available` |
| `strategy.py` | the seven strategies → build plans, all ending in `install/launch/runtime` |
| `package.py` | `aapt2` compile/link, `d8` dex, embed, `apksigner` sign/verify, `selftest` |
| `rust.py` | cargo builds with `rustc --print target-libdir` pre-check per ABI |
| `cpp.py` | CMake host vs NDK configure; refuses ABI flags without a toolchain file |
| `validate.py` | structural APK inspection: manifest, dex magic, ABI dirs, duplicates, alignment |
| `smoke.py` | adb devices/install/launch/logcat; refuses on a broken adb |
| `pipeline.py` | full orchestration + the claims block |

Design rule, enforced in code: **a produced file is not a claim.** The pipeline
emits `artifact_exists`, `signed`, `installed`, `runtime_verified`,
`port_verified`. The last one is hard-coded false; only device evidence moves it
and that judgement is left to a human.

Bugs found and fixed while testing on this host:

- `link` passed `--version-code/--version-name` in the selftest, which requires a
  framework `android.jar`; now omitted by default and only added when supplied.
- `apksigner sign`/`verify` rejected the SHA-256 JAR signature ("not supported on
  API Level(s) 9-17") because the selftest manifest has no `minSdkVersion`.
  Both now take `--min-sdk-version`; `PackageSpec.min_sdk` defaults to 24.
- `selftest` failed on a second run in the same workdir because the keystore
  alias already existed; the stale keystore is now unlinked first. Re-run verified
  idempotent.
- `pipeline._toolchain_stage` read `tools[name]["ok"]` — a key the env report
  does not have — which was a real `KeyError` on the missing-tool path. Now reads
  `["state"] != "ok"`.
- The same class of bug in the `zipalign` check; same fix.

Measured on this host: `pa apk selftest` = 7/7 stages DONE (javac, link, dex,
embed, keystore, sign, verify) and `pa apk validate` on the artifact passes the
package-name check. `zipalign` is absent, so alignment is reported as a warning.

## Phase 8 detail — installer and Termux

`src/pa/install.py`:

- two scopes — `project` (`<project>/.opencode`) and `global`
  (`$XDG_CONFIG_HOME/opencode`, default `~/.config/opencode`);
- a manifest at `<target>/.port-anything-manifest.json` records a sha256 per
  installed file;
- install writes only when the hash differs, removes files that vanished from
  the source tree, and **never** modifies an existing `opencode.jsonc`;
- uninstall removes only manifest-tracked files whose hash still matches, leaves
  hand-edited files in place and reports them, then prunes empty directories.

Verified manually and in `tests/test_install.py`: 114 files installed, second run
reports `unchanged=114`, `--check` clean, an edited file survives uninstall, the
project config survives.

`scripts/termux/` (as the skill ships them):

- `setup.sh` — installs core (`python rust clang cmake ninja git binutils`) and,
  unless `--core-only`, Android tools (`openjdk-17 aapt2 d8 apksigner zipalign`)
  through `pkg`; checks presence first so re-running is safe. No root, no
  systemd, no Docker.
- `doctor.sh` — prints `$HOME`, `$PREFIX`, `$TMPDIR`, tool presence, then
  delegates to `pa apk env`.
- `build-native.sh` — `cargo test` + `cargo build --release` and
  `cmake`/`ctest` for the C++ probe, all under `$TMPDIR`.

All three pass `sh -n`; `doctor.sh` was executed on this host.

Native components:

- C++ `modules/reverse-engineering/native/pa-probe` — C++17, no dependencies,
  bounds-checked reads, static library + CLI + ctest suite. 3/3 ctest pass, CLI
  verified on an ELF fixture, `/bin/sh` and the selftest APK. `-Wall -Wextra
  -Wpedantic` clean.
- Rust `modules/ram-efficiency/native/pa-runtime` — unchanged from Phase 5.

## Phase 6 detail

`pa/agents/`: `contract` (strict task contract with owned files, outputs, done
criteria, prompt renderer), `graph` (Kahn ordering, cycles, layers, readiness,
dependency reachability), `store` (atomic JSON run state, one-way transitions,
claims, stale release, resume), `conflict` (glob-aware claim overlap: literal,
directory, `*`, `**`, conservative on undecidable wildcard pairs), `scheduler`
(dependency + claim + concurrency gate, max 3, deterministic simulation),
`registry` (validates the nine agent files' frontmatter against their filenames).

Nine agent files exist under `.opencode/agents/`: `port-orchestrator` (primary)
plus researcher, reverse-engineer, rust-engineer, cpp-engineer,
android-engineer, graphics-engineer, optimizer, qa-engineer. No model is pinned.

Bugs found and fixed while testing:

- `static_root` dropped the last non-wildcard segment, so `src/rust/**` and
  `src/cpp/**` both rooted at `src` and were reported as conflicting.
- Literal-vs-glob overlap was checked with the root on the wrong side, so a
  literal file below a glob's root was reported as a conflict.

## Phase 5 detail

Python: `ram/monitor.py` reads `/proc/meminfo`, cgroup v1/v2 limits and usage,
`/proc/self/status`, load average and CPU count. A missing source is `None`,
which degrades to sequential — it never guesses a high level.

`ram/policy.py` classifies LOW/MEDIUM/HIGH from ratio **and** absolute floor,
downgrades one step under load, caps concurrency by `reserve_cpu_factor` and
`max_concurrency`, and honours a user override without exceeding the cap.

`ram/manager.py` dispatches through a thread pool, measures before each dispatch,
stops dispatching when the level drops, lets in-flight work finish, and never
signals a process it did not start. `ram/cache.py` is a content-addressed
artifact cache with hash-verified reads, atomic writes, LRU trim and disk-space
eviction confined to its own directory.

Rust: `pa-runtime` is dependency-free, parses `/proc/meminfo` from a `&str`
(testable), reads cgroup limits, and prints the same verdict as Python.

## Phase 4 detail

`pa/tokens/`: `estimate` (heuristic-v1, ±10% ASCII / ±20% CJK), `sources`
(line-indexed manifest with per-file sha256), `context` (budgeted plan: full,
window, index, alias), `guard` (zero-loss verifier), `duplicate` (exact hash
groups + near-similarity), `cache` (memo cache keyed by content hash).

The planner never truncates silently. Every source line is either inside an
included range or listed in `omitted` with a reason, and `pa tokens check`
recomputes the token total from the plan text and fails if it disagrees. A zero
budget indexes every file at 0 tokens rather than dropping it.

## Phase 3 detail

Analyzers registered:

| Analyzer | Formats | Verification |
|---|---|---|
| `apk` | APK, AAB | verified |
| `build_system` | directory | verified |
| `elf` | ELF | verified |
| `gamemaker_data` | GameMaker GEN8 | partially verified |
| `godot_pck` | Godot PCK | verified |
| `love_archive` | LÖVE | verified |
| `macho` | Mach-O, fat | verified |
| `marker_scan` | PE, ELF, Mach-O, APK, unknown | verified |
| `pe` | PE32/PE32+ | verified |
| `renpy_archive` | RPA | verified |
| `source_project` | directory | verified |
| `unity_bundle` | UnityFS | verified |
| `unity_metadata` | IL2CPP metadata | verified |
| `unreal_package` | uasset | partially verified |
| `unreal_pak` | pak footer | partially verified |

Bugs found and fixed while testing against fixtures:

- Mach-O header parsing started at the magic instead of after it, so every field
  was shifted. Fixed in `re/analyzers/macho.py`.
- AXML attributes were read at `chunk + 8 + attributeStart`; the offset is
  relative to the attrExt struct at `chunk + 16`. Fixed in `re/axml.py`.
- The PE fixture initially declared one section, so `.rdata` and the import table
  were invisible. Fixture corrected; the analyzer was already right.

Compatibility engine distinguishes seven routes and keeps stages separate:
`identify`, `source`, `assets`, `toolchain`, `build`, `package`, `install`,
`launch`, `verified`. `port_verified` is hard-coded false until launch and
gameplay evidence exists.

## Measured host facts

- Termux on Android 14 (API 34), aarch64, 8 cores, 7685 MiB RAM, ~1567 MiB
  available → RAM level MEDIUM, concurrency 2.
- rustc/cargo 1.98.1 with `aarch64-linux-android` std installed.
- clang 21.1.8, cmake 4.4.5, ninja 1.13.2, Python 3.14.10, OpenJDK 21.0.12.
- Measured by `pa apk env`: `aapt2` 2.20 (android-16.0.0_r4), `d8` 9.2.4-dev,
  `apksigner` 0.9, `javac` 21.0.12, `keytool` present.
- Missing: Android SDK (`android.jar`), NDK, Gradle, `zipalign`.
- `adb` is present on `PATH` but fails at the loader (`CANNOT LINK EXECUTABLE`,
  missing protobuf symbol) → device tests are `unsupported`, never `passed`.
- `/proc/loadavg` is not readable from the Rust side on this build; load is
  reported null and the memory level alone decides. Measured, not silent.

## Blockers

- Full Gradle/SDK resource linking cannot run here: no `android.jar`. A manifest
  using `android:` attributes is `unsupported` at `resources`/`link`. Manifests
  without them link, which is what the selftest uses.
- `zipalign` is missing, so 4-byte alignment is reported as a warning by
  `validate.inspect` rather than enforced.
- Device smoke tests cannot run because `adb` is broken. `install`, `launch` and
  `runtime` are `unsupported` with the loader error attached.
- A real native game port still needs the NDK to produce `lib*.so` for
  `arm64-v8a`; `ndk-sysroot` alone is not a full NDK.

## Remaining work

1. Framework resource linking when an SDK is present (the SDK path is designed
   and reported, but never executed on a host that has one).
2. Device evidence collection once a working `adb` exists — the only thing that
   can move `port_verified`.
3. Wider engine/version signature coverage in the dataset.
4. `git init` and first commit (not done; no repository initialised yet).

## Commands verified on this host

```sh
./bin/pa doctor
./bin/pa analyze /system/bin/sh
./bin/pa analyze tests/fixtures/fixture.apk --json
./bin/pa compat <target> --markdown report.md
./bin/pa analyzers
./bin/pa dataset validate|stats|index|search|show|manifest|export|import
./bin/pa tokens estimate|index|plan|check|slice|dedupe|cache
./bin/pa ram status|policy|run|cache|probe
./bin/pa agents list|validate|graph|plan|status|prompt
./bin/pa apk env|plan|selftest|validate|smoke|build|native
./bin/pa install --project|--global --dry-run|--check|--uninstall
```