# Port Anything

An autonomous game-porting framework for Android, built to run on Termux as a
first-class host. It reverse-engineers a shipped target, picks one of seven
porting strategies with the evidence attached, builds native code and an APK,
and reports **exactly** what was verified versus what was only produced.

The rule the whole project is built around:

> **An APK existing is not a port.**

Every stage carries its own state — `done`, `failed`, `unsupported`, `skipped`,
`pending` — and `port_verified` stays false until a real device shows the thing
launching and running.

## What is in it

Five modules, one CLI, ~250 tests, no Python dependencies beyond the standard
library.

| Module | Job | Commands |
|---|---|---|
| Reverse engineering | format, engine, architecture, protection, compatibility ranking | `pa analyze`, `pa compat`, `pa analyzers` |
| Token efficiency | lossless context planning — no line dropped | `pa tokens estimate/index/plan/check/slice/dedupe/cache` |
| RAM efficiency | measured memory policy, adaptive concurrency | `pa ram status/policy/run/cache/probe` |
| Multi-agent | nine contracted agents, DAG, run store, claim conflicts | `pa agents list/validate/graph/plan/status/prompt` |
| APK build | toolchain discovery, packaging, validation, device smoke | `pa apk env/plan/selftest/validate/smoke/build/native` |

Plus: an installer that preserves your model selection, a sourced dataset of 128
records with a generated SQLite index, a dependency-free Rust runtime, a C++
header probe, and Termux scripts that build and test both.

## The seven porting strategies

`already_android`, `native_port`, `engine_recompile`, `adaptation`,
`asset_migration`, `compatibility_layer`, `emulator`.

They are not interchangeable. An emulator APK is a launcher; a
compatibility-layer APK carries a translation runtime; a native port rebuilds
the code. `pa compat <target>` scores each route against what the artifact
actually is and what this host can actually build; `pa apk plan` turns the
winner into a build plan ending in the device-evidence tail.

## Quick start

```sh
./bin/pa doctor                 # environment and real capabilities
./bin/pa analyze <target>       # format, engine, arch, protection
./bin/pa compat <target>        # ranked strategies with rationale
./bin/pa apk env                # Android toolchain and its limits
./bin/pa apk selftest           # prove aapt2+d8+apksigner on this host
./bin/pa install --project      # install into ./.opencode
```

Termux:

```sh
scripts/termux/setup.sh          # toolchains through pkg, no root
scripts/termux/doctor.sh         # shell facts + tool presence
scripts/termux/build-native.sh   # cargo test + cmake/ctest under $TMPDIR
```

## Honest state

The build pipeline reports per stage and never collapses them:

```
toolchain → resources → link → dex → native → embed → align → sign
          → verify → inspect → devices → install → launch → runtime
```

- A missing tool makes the stages that need it `unsupported`, naming the tool.
- `sign` without a keystore is `unsupported`, never a silent unsigned "success".
- `runtime` is `unsupported` when `adb` cannot execute, with the loader error
  attached rather than hidden.
- `port_verified` is hard-coded false from the pipeline. Only device evidence
  moves it, and that judgement is not made for you.

The same discipline applies to the dataset: 128 records, every one with a named
source and a verification status. No fabricated entries.

## Layout

```
.opencode/
  agents/                       nine agent definitions (one primary, eight specialists)
  skills/port-anything/
    SKILL.md                    entry point
    src/pa/                     Python package, stdlib only
    modules/                    per-module docs + native code (Rust, C++)
    references/                 strategy matrix, Termux notes, house rules
    datasets/                   PAKB records, schemas, sources, SQLite index
    scripts/                    pa.py + Termux helpers
    templates/                  project config, APK spec, task manifest
    tests/                      unit + integration suites
bin/pa                          POSIX launcher
opencode.jsonc                  project config, no model pinned
```

## Install

```sh
pa install --project        # into ./.opencode; leaves an existing opencode.jsonc alone
pa install --global         # into ~/.config/opencode
pa install --dry-run        # show what would change
pa install --check          # verify an install is current
pa install --uninstall      # remove only files the installer tracked
```

Idempotent. Non-destructive: uninstall skips anything you edited by hand and
reports it. No model or provider is pinned anywhere — agents inherit whatever
you have selected.

## Test

```sh
python3 -m unittest discover -s .opencode/skills/port-anything/tests -p 'test_*.py'
cd .opencode/skills/port-anything/modules/ram-efficiency/native/pa-runtime && cargo test --offline
cmake -S .opencode/skills/port-anything/modules/reverse-engineering/native/pa-probe -B build/probe && cmake --build build/probe && ctest --test-dir build/probe
```

CI runs all three on Linux, plus `pa dataset validate`.

## Limitations

- Full Gradle/SDK resource linking is not implemented on a host with no
  framework `android.jar`; the pipeline reports `unsupported` instead of
  emitting an APK that cannot carry its own manifest.
- `zipalign` is required but optional in the pipeline; alignment is reported as
  a warning when the tool is absent.
- Device smoke tests need a working `adb`; the fallback path is honest refusal,
  not a fake pass.
- Dataset coverage is broad but shallow per engine; unknown versions are
  reported as unknown with the evidence attached.

## License

MIT. See `LICENSE`.

Porting software you own or are authorized to modify is in scope. This project
ships no game assets, keys, or proprietary binaries, and implements no DRM or
anti-cheat circumvention.