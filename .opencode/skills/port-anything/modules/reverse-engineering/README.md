# Reverse engineering

Identify what a shipped target actually is before deciding how to port it.
Nothing here guesses: every field is read from the bytes, and a field that
cannot be read is reported as unknown with the reason.

## What it produces

`pa analyze <path>` returns a bundle:

- **format** — `sniff` reads the magic and container (PE, ELF, Mach-O, APK, AAB,
  Unity AssetBundle, Godot `.pck`, GameMaker `data.win`, Ren'Py `.rpa`, LÖVE
  `.love`, Unreal `.pak`, ISO9660), including the APK/AAB/AXML path.
- **analyzers** — per-format detail: PE imports/exports/sections, ELF
  interpreter and SONAME, Mach-O slices, AXML manifest, game-container layouts,
  source-tree signals, and a bounded strings scan.
- **fingerprint** — engine and engine version, architecture, asset layout,
  protection indicators.
- **compatibility** — the recommended strategy plus a per-strategy verdict.

`pa compat <path>` prints just the compatibility ranking. `pa analyzers` lists
the registered analyzers and their stated limits.

## Layout

```
src/pa/re/
  sniff.py            magic-byte container detection
  analyzers/          pe, elf, macho, apk, gameformats, source, strings_scan
  axml.py             binary AndroidManifest.xml parser
  fingerprint.py      engine / architecture / protection
  compat.py           7 strategies, 9 stage names, assess()
  report.py           text and Markdown renderers
```

## Limits

- Protection detection is indicative, not a proof. It flags packer sections,
  entropy, and known-loading patterns; it does not defeat anything.
- The strings scan is bounded (size gate + cap) so a large or hostile file
  cannot exhaust memory.
- Engine fingerprinting recognises versions it has signatures for and says
  "unknown, here is the evidence" otherwise.

## Tests

`tests/test_re.py` builds fixtures for every container (`tests/fixtures/
make_fixtures.py`) and asserts the sniff result, the analyzer output, and the
compatibility ranking. Run from the skill directory:

```sh
python3 -m unittest tests.test_re
```
