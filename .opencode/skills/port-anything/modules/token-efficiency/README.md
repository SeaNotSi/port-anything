# Token efficiency

Keep large porting contexts inside a model's budget **without losing a line**.
The rule is absolute: no blind truncation, no dropped source, no summary that
stands in for the original. A summary is an index; the original stays reachable
and verifiable.

## How it works

1. **Index** every source: path, content hash, line count, token estimate.
2. **Plan** a context under a token budget. The plan is a list of typed
   references — `full`, `slice` (exact line range), or `index` (a heading list
   plus a pointer) — chosen so the plan's own metadata and the referenced lines
   fit the budget.
3. **Check** the plan: every line of every source is either included or
   covered by a `slice`/`index` reference. A plan that drops a line without a
   reference fails the check.

## Commands

```sh
pa tokens estimate <path>...              # per-file token estimates
pa tokens index <path>...                 # write a source index (hashes, lines)
pa tokens plan --budget N <path>...       # build a lossless plan
pa tokens check --plan plan.json          # verify nothing was dropped
pa tokens slice <source> --lines A-B      # retrieve an exact range
pa tokens dedupe <path>...                # exact and near-duplicate sources
pa tokens cache --stats|--clear           # inspect or clear the estimate cache
```

## Guarantees

- **No capability loss.** Compression is structural (references + dedupe of
  byte-identical sources), never lossy rewriting of content.
- **Deterministic.** The same sources and budget produce the same plan; the
  cache is keyed by content hash plus tool version plus environment
  fingerprint.
- **Verifiable.** `pa tokens check` is the acceptance test: a plan that cannot
  account for every source line is rejected.

## Configuration

`config/token-efficiency.json` holds the estimator constants and default
budget. Invalid values fall back to the built-in defaults rather than raising.

## Tests

`tests/test_tokens.py` covers estimation, planning under budget, the check that
fails on a dropped line, slicing, and deduplication.

```sh
python3 -m unittest tests.test_tokens
```
