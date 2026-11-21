# Porting strategies

Seven routes. They are not interchangeable, and choosing the wrong one is the
most common way a project ends up with an APK that is not a port.

| Strategy | When it applies | What you are actually building |
|---|---|---|
| `already_android` | The artifact is already an Android package. | Adaptation and verification, not a port. |
| `native_port` | Source is available and no engine is in the way. | The source, rebuilt for Android. |
| `engine_recompile` | Source plus a supported engine export path. | A re-export from the engine's Android target. |
| `adaptation` | Binary only, engine runtime is replaceable. | Patched platform glue around the original code. |
| `asset_migration` | Binary only, assets are extractable. | A new runtime built around the extracted assets. |
| `compatibility_layer` | Foreign-architecture binary. | A CPU + OS translation layer plus the original. |
| `emulator` | Disc or console image. | An emulator core plus a launcher front-end. |

## Stage vocabulary

Every strategy tracks the same nine stages, each with its own state:

```
identify → source → assets → toolchain → build → package → install → launch → verified
```

`port_verified` (the `verified` stage) is false until a launch and a gameplay
check are recorded. Packaging success never promotes it.

## How the recommendation is made

`pa compat <target>` scores each strategy from what `pa analyze` found — format,
engine, architecture, protection, available source, host capabilities (SDK, NDK,
cross toolchain) — and returns a recommended strategy with:

- **rationale** — the evidence supporting it,
- **requirements** — the toolchain and inputs it needs,
- **blockers** — host capabilities that are missing,
- **evidence** — the specific fields the decision was read from.

`pa apk plan --target <path>` feeds that recommendation into the build plan;
`pa apk plan --strategy <id>` plans a strategy you have chosen yourself.

## Build plans per strategy

Each strategy maps to a `BuildPlan` (see `src/pa/apk/strategy.py`) with a step
list, whether source is required, and the native build kind. All plans end in
the device-evidence tail (`install`, `launch`, `runtime`), because a strategy is
not finished at `package`.

## Reference engines

`engine_recompile` recognises the engines with a supported Android export path
(Godot, LÖVE, Cocos2d-x, Unreal, Bevy, MonoGame, libGDX, Ogre, id Tech, Source,
RPG Maker, Defold). Heavy engines are flagged so the plan sets expectations
about build cost. Engines with no source route fall back to `emulator` or
`compatibility_layer`.
