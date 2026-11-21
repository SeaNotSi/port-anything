# RAM efficiency

Run parallel work on a phone without driving the device into the out-of-memory
killer. Concurrency is decided by **measured** free memory and load, not by a
guess or a fixed core count.

## Policy

A sample of available memory and load average maps to a level:

- **LOW** — run sequentially.
- **MEDIUM** — limited concurrency (a small ceiling).
- **HIGH** — controlled concurrency, still capped.

The decision is bounded twice: by a ratio of free memory with an absolute
floor, and by `max_concurrency` scaled by `reserve_cpu_factor` so a burst never
claims every core. A user override always wins.

## Commands

```sh
pa ram status                     # current sample and the concurrency decision
pa ram policy                     # thresholds, concurrency map, live verdict
pa ram run --jobs N -- <cmd...>   # run jobs with adaptive concurrency
pa ram cache --stats|--trim|--clear
pa ram probe --seconds N          # sample over time and report
```

`pa ram run` dispatches real subprocesses through the policy, keeping at most
the decided number in flight, and never kills a process it did not start.

## Native runtime

`modules/ram-efficiency/native/pa-runtime` is a dependency-free Rust crate that
implements the same policy for native callers. It has no crate dependencies, so
it builds and tests offline:

```sh
cd modules/ram-efficiency/native/pa-runtime
cargo test --offline            # 17 unit + 4 integration tests
cargo build --release --offline
./target/release/pa-runtime --json
```

`/proc/loadavg` is not readable on every Termux build; when it is, the load
figure is used, otherwise it is reported as null and the memory level alone
decides. That is a measured fallback, not a silent one.

## Configuration

`config/ram-efficiency.json` holds the ratios, floors and concurrency map.
Invalid values fall back to defaults.

## Tests

`tests/test_ram.py` drives the monitor, policy, manager (real parallel dispatch)
and cache with injected samples, plus the probe. The Rust crate carries its own
`cargo test` suite.

```sh
python3 -m unittest tests.test_ram
scripts/termux/build-native.sh
```
