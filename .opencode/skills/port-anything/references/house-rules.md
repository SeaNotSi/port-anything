# Port Anything — house rules for agents

These rules are loaded into every session in this project. They are hard
constraints, not suggestions.

## Scope

1. Work only on software the user owns or is authorized to modify. If
   authorization is unclear, stop and ask once, then proceed only with what
   was granted.
2. Never implement DRM circumvention, anti-cheat defeat for online games,
   license-check removal, or distribution of copyrighted assets. Porting an
   owned copy is in scope; piracy is not.
3. Never include game assets, keys, or proprietary binaries in the repository.
   Sample payloads stay under `samples/` (git-ignored) and are user-supplied.

## Truthfulness

4. An APK existing is not a port. Report each validation stage separately:
   built, signed, installed, launched, rendered, played.
5. Never write "tested" for something that was not executed on this machine.
   Use TESTED / SIMULATED / PARTIALLY TESTED / NOT TESTED / UNSUPPORTED.
6. If a tool is missing (NDK, Gradle, SDK), say so and take the fallback path
   or stop. Do not fabricate output paths.
7. Dataset records must carry a real source reference and a verification
   status. Do not invent versions, requirements, or compatibility claims.

## Engineering

8. Rust and C++ code must compile with `-Wall -Wextra` / `cargo clippy` clean
   or the warning must be explained in the commit/notes.
9. Every subprocess call uses an argument vector, never a shell string built
   from user input.
10. Every filesystem write resolves and checks the path stays inside the
    project or an explicitly allowed directory.
11. Bounded loops over external data. Size gates before allocation. Fail
    closed on malformed input.
12. No placeholder implementations in shipped modules. If something cannot be
    implemented, it is documented as unsupported and the code refuses to run
    it — not silently returns fake data.

## Token efficiency

13. Never truncate source material to save tokens. Summaries are indexes;
    the original stays reachable.
14. Cache by content hash plus tool version plus environment fingerprint.
    Stale cache is worse than no cache.
15. If an optimization changes a result, disable the optimization and record
    the regression.

## Agents

16. The orchestrator owns the task graph and the merge. Workers own their
    files and nothing else.
17. Two or three concurrent subagents maximum, decided by measured free RAM.
18. A worker that cannot finish reports the blocker; it does not invent a
    result.
