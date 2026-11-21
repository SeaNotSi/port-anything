---
name: Bug report
about: Something built, claimed, or reported wrong
labels: [bug]
---

## What happened

<!-- The observed behaviour, verbatim output included. -->

## What you expected

## Environment

- Host: <!-- Termux on Android 14 / Ubuntu 24.04 / macOS 15 ... -->
- `$PREFIX`:
- `$TMPDIR`:
- Python: <!-- python3 --version -->
- Relevant tools: <!-- aapt2 --version, cargo --version, adb version, ... -->

## Reproduce

```
# exact commands
```

## Stage states

If this is a build-pipeline report, paste the stages that came back wrong:

```
toolchain  : done
resources  : unsupported (no framework jar)
...
```

## Notes

- [ ] This is about a claim (a stage said done/failed when it should not have)
- [ ] This is a crash or traceback
- [ ] This is about dataset content