# Pull request

## What changed

<!-- One paragraph. What and why. -->

## Scope

- [ ] Python (`src/pa/`, `tests/`)
- [ ] Rust runtime (`modules/ram-efficiency/native/pa-runtime`)
- [ ] C++ probe (`modules/reverse-engineering/native/pa-probe`)
- [ ] Dataset (`datasets/`)
- [ ] Agents (`agents/`, `src/pa/agents/`)
- [ ] Docs / config / CI

## Evidence

Paste the commands you ran and what they printed. "Should work" is not evidence.

```
$ python3 -m unittest discover -s .opencode/skills/port-anything/tests -p 'test_*.py'
...
```

- [ ] Python suite passes
- [ ] `cargo test --offline` passes (if touched)
- [ ] `ctest` passes (if touched)
- [ ] `pa dataset validate` is clean (if touched)

## Honesty check

- [ ] No stage is marked `done` without the command and artifact behind it
- [ ] No `port_verified` was promoted without device evidence
- [ ] No fabricated dataset records, no guessed licenses
- [ ] No model or provider pinned in config or agent frontmatter
- [ ] No game assets, keys, or proprietary binaries added

## Notes for the reviewer

<!-- Anything you could not verify on your host, and why. -->