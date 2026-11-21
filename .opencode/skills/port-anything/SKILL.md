---
name: port-anything
description: Autonomous game-porting framework. Reverse-engineers a shipped game or app, chooses one of seven porting strategies, builds native code and an Android APK, and reports exactly what was verified versus what was only built. Includes token-efficient context planning, measured RAM policy, a nine-agent orchestration layer, and a sourced knowledge dataset. Termux/Android first-class.
---

# Port Anything

A working framework for porting software to Android. It is built from five
modules that each do one job, connected by a CLI (`pa`) and a small Python
package (`pa`) that uses only the standard library.

The framework's central rule: **an APK existing is not a port.** Every stage
carries its own state (`done` / `failed` / `unsupported` / `skipped` /
`pending`), and `port_verified` stays false until there is runtime evidence
from a real device.

## Quick start

```sh
./bin/pa doctor                # environment and capabilities
./bin/pa analyze <target>      # format, engine, architecture, protection
./bin/pa compat <target>       # rank the seven porting strategies
./bin/pa apk env               # Android packaging toolchain and its limits
./bin/pa apk selftest          # prove aapt2+d8+apksigner work on this host
./bin/pa install --project     # install the skill into ./.opencode
```

## The five modules

| Module | What it does | Entry points |
|---|---|---|
| Reverse engineering | Formats, engines, architecture, protection, compatibility ranking | `pa analyze`, `pa compat`, `pa analyzers` |
| Token efficiency | Lossless context planning: every source line stays accounted for | `pa tokens estimate/index/plan/check/slice/dedupe/cache` |
| RAM efficiency | Measured memory policy and adaptive concurrency | `pa ram status/policy/run/cache/probe` |
| Multi-agent | Nine contracted agents, DAG, persistence, conflict detection | `pa agents list/validate/graph/plan/status/prompt` |
| APK build | Toolchain discovery, packaging, validation, device smoke test | `pa apk env/plan/selftest/validate/smoke/build/native` |

Each module has a README under `modules/<name>/` with its design, its limits and
how it is tested.

## The seven porting strategies

`already_android`, `native_port`, `engine_recompile`, `adaptation`,
`asset_migration`, `compatibility_layer`, `emulator`. `pa compat` scores each
against the artifact and the host's proven capabilities; `pa apk plan` turns the
chosen one into a build plan. See `references/porting-strategies.md`.

## Honest state

The build pipeline reports per stage and never collapses them:

- `toolchain` → `resources` → `link` → `dex` → `native` → `embed` → `align` →
  `sign` → `verify` → `inspect` → `devices` → `install` → `launch` → `runtime`
- A missing tool makes the stages that need it `unsupported`, naming the tool.
- `sign` without a keystore is `unsupported`, not a silent unsigned success.
- `runtime` is `unsupported` when `adb` cannot execute, and the loader error is
  attached rather than hidden.

## Layout

```
.opencode/
  skills/port-anything/
    SKILL.md              this file
    src/pa/               Python package (stdlib only)
    modules/              per-module docs + native code
    references/           strategy matrix, Termux notes, house rules
    datasets/             PAKB: 128 sourced records (JSONL + SQLite index)
    scripts/              pa.py + Termux helpers
    templates/            project config, APK spec, task manifest
    tests/                unit + integration suites
  agents/                 the nine agent definitions
```

## Installing

```sh
pa install --project          # into ./.opencode (leaves opencode.jsonc alone)
pa install --global           # into ~/.config/opencode
pa install --dry-run          # show what would change
pa install --check            # verify an install is current
pa install --uninstall        # remove only files the installer tracked
```

No model is pinned anywhere. Agents inherit whatever model the operator selects.

## Termux

`scripts/termux/setup.sh` installs the toolchains through `pkg` (no root);
`scripts/termux/doctor.sh` prints the shell facts and tool presence;
`scripts/termux/build-native.sh` builds and tests the Rust crate and the C++
probe entirely under `$TMPDIR`. See `references/termux.md`.

## Testing

```sh
python3 -m unittest discover -s tests -p 'test_*.py'   # from this directory
```

The C++ probe and the Rust runtime carry their own suites (`ctest`, `cargo
test`); `scripts/termux/build-native.sh` runs both.

## What this is not

- It is not a claim that any specific game runs. It is the machinery to do the
  port and the honesty to report where it got to.
- It does not ship game assets, keys or proprietary binaries.
- It does not fabricate dataset records: every record names a source and a
  verification status.
