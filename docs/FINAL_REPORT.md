# Port Anything — final report

Date: 2026-11-22
Commit: `cb665e5` (138 files, 18,789 lines)
Host: Termux on Android 14 (API 34), aarch64, 8 cores, 7.5 GiB RAM

---

## 1. What was built

A working autonomous game-porting framework, delivered as files that run:
a Python package (stdlib only), a dependency-free Rust crate, a C++17 probe, a
nine-agent OpenCode layer, a sourced dataset, an installer, Termux scripts,
tests, docs and CI. Not a description of a framework — the framework.

The organising rule, enforced in code rather than stated in prose:

> **An APK existing is not a port.** Every stage carries its own state, and
> `port_verified` is hard-coded false until device evidence exists.

## 2. Repository structure

```
port-anything/
├── README.md                      project front page
├── LICENSE                        MIT
├── CONTRIBUTING.md                ground rules, dev + test commands
├── IMPLEMENTATION_STATUS.md       phase ledger with measured evidence
├── opencode.jsonc                 V2 config; no model pinned
├── bin/pa                         POSIX launcher (no /usr/bin/env assumption)
├── docs/
│   ├── ENVIRONMENT_REPORT.md      Phase 0 host inventory
│   └── FINAL_REPORT.md            this file
├── .github/
│   ├── workflows/ci.yml           4 jobs: python, rust, cpp, shell syntax
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/{bug_report,feature_request}.md, config.yml
└── .opencode/
    ├── agents/                    9 definitions (1 primary, 8 specialists)
    └── skills/port-anything/
        ├── SKILL.md
        ├── src/pa/                72 Python files
        ├── modules/               5 module READMEs + native code
        ├── references/            strategies, Termux, house rules
        ├── datasets/              128 records, 10 JSONL, 5 schemas, 44 sources
        ├── scripts/               pa.py + termux/{setup,doctor,build-native}.sh
        ├── templates/             project config, APK spec, task manifest
        └── tests/                 6 suites + fixture generator
```

## 3. Modules

### 3.1 Reverse engineering (`src/pa/re/`)

15 registered analyzers over PE, ELF, Mach-O, APK/AAB, binary AXML, Unity
AssetBundle, IL2CPP metadata, Godot PCK, GameMaker `data.win`, Ren'Py RPA,
LÖVE, Unreal pak/uasset, source trees, plus a bounded strings scan.

Outputs: format + certainty, engine + version with the bytes that prove it,
architecture, protection indicators, asset layout, and a ranked compatibility
verdict. Commands: `pa analyze`, `pa compat`, `pa analyzers`.

Stages are never collapsed: `identify → source → assets → toolchain → build →
package → install → launch → verified`.

### 3.2 Token efficiency (`src/pa/tokens/`)

Zero-loss context planning. A plan is a list of typed references — `full`,
`slice` (exact line range), `index` (heading list + pointer) — and
`pa tokens check` recomputes the token total from the plan and fails if any
source line is unaccounted for. No blind truncation, no weaker model, no dropped
source. A zero budget indexes every file at 0 tokens rather than dropping it.

Commands: `estimate|index|plan|check|slice|dedupe|cache`.

### 3.3 RAM efficiency (`src/pa/ram/` + Rust)

Measured, not guessed: `/proc/meminfo`, cgroup v1/v2, load average, CPU count.
Levels LOW/MEDIUM/HIGH with an absolute memory floor **and** a ratio; downgrade
one step under load; cap by `reserve_cpu_factor` and `max_concurrency`; user
override wins. The runner measures before each dispatch, stops dispatching when
the level drops, lets in-flight work finish, and never signals a process it did
not start.

`modules/ram-efficiency/native/pa-runtime` — Rust, no crate dependencies, builds
offline, same verdict as Python, `--json` output.

### 3.4 Multi-agent (`src/pa/agents/`, `.opencode/agents/`)

Nine agents: `port-orchestrator` (primary) plus researcher, reverse-engineer,
rust-engineer, cpp-engineer, android-engineer, graphics-engineer, optimizer,
qa-engineer. Every unit is a strict `TaskContract` — objective, scope in/out,
inputs, outputs, owned paths, done criteria, dependencies — rejected at parse
time if it has no completion criterion or writes outside its claims.

Conflict detection is glob-aware and conservative: a conflicting unit defers,
never gets forced. Concurrency ceiling 2–3, decided by the RAM policy. Run state
persists atomically to JSON so a killed process resumes.

Commands: `list|validate|graph|plan|status|prompt`. No model pinned anywhere.

### 3.5 APK build (`src/pa/apk/`)

Two paths, honestly separated:

- **SDK/Gradle path** — designed and reported, not executable on this host.
- **`aapt2` + `d8` + `apksigner` fallback** — implemented and proven.

Eleven modules: `stages`, `env`, `strategy`, `package`, `rust`, `cpp`,
`validate`, `smoke`, `pipeline`, plus `validate`/`smoke` internals.

Stage vocabulary: `done` / `failed` / `unsupported` / `skipped` / `pending`.
`unsupported` names the missing dependency and the consequence.

Claims block on every run: `artifact_exists`, `signed`, `installed`,
`runtime_verified`, `port_verified` — the last always false.

Commands: `env|plan|selftest|validate|smoke|build|native`.

## 4. Dataset (PAKB)

- **128 records** across 10 categories: engine 19, fileformat 19, platform 17,
  cpp 15, graphics 12, rust 12, android 11, toolchain 10, buildsystem 9,
  architecture 4.
- Verification: **108 verified, 20 partially verified**, 0 fabricated.
- **44 sources** in a registry; every record names one.
- Formats: canonical **JSONL** (`datasets/records/`) + generated **SQLite** index
  with FTS5 when available + JSON Schema + Markdown + JSON manifest.
- **5 schemas**: record, engine, platform, graphics, toolchain.
- Validation: `ok True, errors 0, warnings 3` — the three warnings are
  `NOASSERTION` licenses on `platform.linux_pc`, `platform.web`,
  `toolchain.termux`. `NOASSERTION` is honest; a guessed license would not be.
- The index is generated and **not committed**; `pa dataset index` rebuilds it
  (`needs_rebuild` compares schema + dataset version + hashes).

Commands: `validate|search|show|stats|index|manifest|export|import`.

## 5. Installer

`pa install` with `--project`, `--global`, `--dry-run`, `--check`,
`--uninstall`, `--json`, `--path`.

- Manifest at `<target>/.port-anything-manifest.json`, sha256 per file.
- Writes only when the hash differs; removes files that left the source tree.
- **Never modifies an existing `opencode.jsonc`** — the user's model selection
  is preserved. A missing config is seeded from `templates/opencode.project.jsonc`,
  which pins no model.
- Uninstall removes only manifest-tracked files whose hash still matches; a
  hand-edited file is left in place and reported; empty dirs pruned.

Measured: **126 files** installed (skill + 9 agents + dataset), second run
`unchanged=126`, `--check` clean, edited file survived uninstall, project config
survived.

## 6. Termux compatibility

First-class, measured on the reference device:

| Verified present | Verified missing / broken |
|---|---|
| python3 3.14.10, git, binutils | Android SDK — **no `android.jar`** |
| rustc/cargo 1.98.1 (`aarch64-linux-android` std) | Android NDK — none |
| clang/clang++ 21.1.8, cmake 4.4.5, ninja 1.13.2 | gradle — none |
| OpenJDK 21.0.12 (`javac`, `keytool`) | zipalign — none |
| aapt2 2.20, d8 9.2.4-dev, apksigner 0.9 | adb — **broken at the loader** |

Rules honoured throughout: `$PREFIX`, `$HOME`, `$TMPDIR` used explicitly; no
`/usr/bin/env` in launchers; no root, no systemd, no Docker; all build output
under `$TMPDIR` in tests; caches under `$XDG_CACHE_HOME`/`$HOME/.cache`; nothing
teleported outside the project.

Scripts: `setup.sh` (pkg, presence-checked, idempotent), `doctor.sh` (shell facts
+ tool presence + `pa apk env`), `build-native.sh` (cargo + cmake/ctest under
`$TMPDIR`). All pass `sh -n`; `doctor.sh` executed.

## 7. Test results

| Suite | Count | Result |
|---|---|---|
| Python (unittest, 6 files) | **256** | OK |
| ├ reverse engineering | 21 | OK |
| ├ token efficiency | 28 | OK |
| ├ RAM efficiency | 55 | OK |
| ├ multi-agent | 69 | OK |
| ├ APK build | 68 | OK |
| └ installer | 15 | OK |
| Rust (`cargo test --offline`) | 17 unit + 4 integration | OK |
| C++ (`ctest`) | 3 | OK |

Also executed end to end: `pa apk selftest` — **7/7 stages DONE**
(javac → link → dex → embed → keystore → sign → verify), idempotent on re-run;
`pa apk validate` passes the package-name check on the produced APK;
`pa dataset validate/index/stats`; `pa agents validate` (9/9);
`pa ram status`; `pa install` all five modes.

Bugs found by testing and fixed:

- Mach-O header parsed from the magic instead of after it — every field shifted.
- AXML attribute offset was relative to the chunk, not the attrExt struct.
- `pipeline` read `tools[name]["ok"]` (a key that does not exist) — real
  `KeyError` on the missing-tool path; same bug in the `zipalign` check.
- `aapt2 link` passed `--version-code/--version-name`, which needs a framework
  jar; now omitted unless supplied.
- `apksigner sign/verify` rejected SHA-256 JAR signatures on an API<18 default;
  both now pass `--min-sdk-version` (spec default 24).
- Selftest was not idempotent (stale keystore alias); fixed and verified.
- Claim-graph `static_root` dropped the last non-wildcard segment, producing
  false conflicts between `src/rust/**` and `src/cpp/**`.

## 8. Limitations — stated, not hidden

1. **No framework resource linking on this host.** A manifest using `android:`
   attributes is `unsupported` at `resources`/`link` with the missing
   `android.jar` named. The SDK path exists in code but has never been executed
   on a machine that has an SDK.
2. **No `zipalign`.** Alignment is reported as a warning by `validate.inspect`,
   not silently assumed.
3. **No device evidence.** `adb` cannot execute, so `install`/`launch`/`runtime`
   are `unsupported` with the loader error attached. Nothing is reported as
   "tested" on a device.
4. **No real game port performed.** This is the machinery plus the honesty, not
   a claim that a specific title runs. A real native port still needs the NDK.
5. **Engine signature coverage is broad but shallow** — unknown versions are
   reported as unknown with the evidence attached.
6. **Desktop Linux/macOS untested** — CI is configured for them, but this
   session only measured Termux.
7. **Dataset warnings** — three `NOASSERTION` licenses, left as-is rather than
   invented.

## 9. Remaining work

1. Execute the SDK/Gradle path on a host with `android.jar` and an NDK; confirm
   `android:` attribute resolution and package it as a second selftest.
2. Move `port_verified` with real device evidence once `adb` works — the
   intended next stage, and the only thing allowed to promote it.
3. Widen engine/version signatures in PAKB, and add graphics-API and toolchain
   records for NDK/Gradle versions.
4. Add `zipalign` to the `pkg` set and make `align` a hard requirement when the
   spec asks for it.
5. Publish: the repository is initialised with one commit on `main` and is
   push-ready; remote, tags and release notes are not created.

## 10. Bottom line

Five modules, each real. 256 + 21 native tests green on the target device. Every
state in every report is traceable to a command that ran. The framework's answer
to "did it work?" is a stage table, not a sentence — which is the only answer
worth shipping.