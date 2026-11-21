# APK build

Package an Android application from a manifest, resources, dex and native
libraries, then prove what the artifact actually is. The module's design rule is
that a produced file is not a claim: each stage reports its own state and the
report separates packaging from installation from runtime.

## Two build paths

- **SDK/Gradle path** — used when a platform `android.jar`, an NDK and `gradle`
  are present. Required to compile resources that use `android:` attributes.
- **`aapt2` + `d8` + `apksigner` fallback** — used otherwise. It links a
  manifest, dexes classes, embeds dex/native/asset entries, and signs. It cannot
  resolve `android:` attributes without the framework jar, and it says so
  (`apk_framework_resources: unsupported`) instead of producing a broken APK.

## Commands

```sh
pa apk env                      # discover the toolchain and its real limits
pa apk plan --strategy native_port   # or --target <path> to use its recommendation
pa apk selftest                 # build+sign a minimal APK to prove the mechanics
pa apk validate app.apk [--package dev.x]
pa apk smoke app.apk --package dev.x
pa apk build --spec spec.json   # full pipeline; emits the claims block
pa apk native --dir . --kind rust|--kind cpp --abi arm64-v8a
```

`templates/apk-project-spec.json` is the spec shape for `pa apk build`.

## Stage states

`done` / `failed` / `unsupported` / `skipped` / `pending`. `unsupported` names
the missing dependency and the consequence. Examples on a host without an SDK:

- `resources` and `link` with `android:` attributes → `unsupported` (no framework jar)
- `align` → `unsupported` (`zipalign` missing)
- `sign` → `unsupported` (no keystore in the spec)
- `install` / `launch` / `runtime` → `unsupported` (`adb` cannot execute)

## The claims block

Every pipeline run ends with:

```
artifact_exists, signed, installed, runtime_verified, port_verified
```

`port_verified` is **always false** from this module. It becomes true only when a
device run produces launch and gameplay evidence, which is a human judgement the
pipeline does not make for you.

## Native builds

- Rust: `pa/apk/rust.py` checks `rustc --print target-libdir` for the ABI's
  target before cargo runs, so a missing target is a named blocker, not a wall
  of linker output. ABI → target mapping is in `ABI_TARGETS`.
- C++/NDK: `pa/apk/cpp.py` configures with `android.toolchain.cmake` when an NDK
  is found, and refuses the NDK ABI flags when it is not. A host build still
  runs for tests but its output is not Android code.

## Tests

`tests/test_apk.py` fakes the process layer to assert the *decisions* (which
command is built, which state results), inspects a synthetic APK, covers the
`adb`-broken smoke paths, and — only when the host actually has the tools —
runs `selftest` end to end.

```sh
python3 -m unittest tests.test_apk
```
