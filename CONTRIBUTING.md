# Contributing to Port Anything

## Ground rules

1. **Truthfulness beats features.** Never mark a stage `done` without the
   command that produced it and the artifact it left. Never write "tested" for
   something that was not executed on the machine you ran it on.
2. **An APK existing is not a port.** Do not promote `port_verified`. It moves
   only on device evidence.
3. **No fabricated dataset records.** Every record needs a real source and a
   verification status. `NOASSERTION` is a valid license value; a guessed one
   is not.
4. **No pinned models.** `opencode.jsonc` and the agent definitions must not
   carry a `model` or `provider` field. Agents inherit the operator's choice.
5. **No assets.** No game files, keys, or proprietary binaries in the
   repository. Local payloads go under `samples/`, which is git-ignored.

## Development

```sh
# Python — stdlib only, no venv needed
python3 -m unittest discover -s .opencode/skills/port-anything/tests -p 'test_*.py'

# Rust runtime
cd .opencode/skills/port-anything/modules/ram-efficiency/native/pa-runtime
cargo test --offline

# C++ probe
cmake -S .opencode/skills/port-anything/modules/reverse-engineering/native/pa-probe \
      -B /tmp/pa-probe && cmake --build /tmp/pa-probe && ctest --test-dir /tmp/pa-probe

# Dataset
./bin/pa dataset validate
./bin/pa dataset index
```

## Style

- Python: type hints, `from __future__ import annotations`, no third-party
  imports. Subprocess calls go through `pa.util.proc.run` with an argument
  vector — never `shell=True` — so they can be faked in tests.
- C++: C++17, `-Wall -Wextra -Wpedantic` clean, bounds-checked reads, no
  dependencies.
- Rust: no crate dependencies (the crate must build offline).
- Comments explain *why* — the constraint, the decision, the failure a guard
  catches. Not what the next line does.

## Tests

- Shelling-out code is tested against a fake `proc.run` that asserts the
  decision (which argv, which stage state), not against an installed tool.
- Host-dependent tests skip explicitly when the tool is absent. A skip is not a
  pass and the suite must say so.
- New behaviour needs a test that would fail without it.

## Pull requests

- One concern per PR.
- Say what you measured. "128 tests pass" beats "should work".
- If a stage remains `unsupported` on your host, leave it that way and name the
  missing dependency. Do not paper over it.
- Update `IMPLEMENTATION_STATUS.md` if you change a phase's state.

Use the PR template. Small, verified diffs get merged fastest.