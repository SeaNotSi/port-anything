# Multi-agent orchestration

Nine agents, one contract, a DAG you can inspect, and a run store you can
resume from. The orchestrator decomposes and merges; the specialists own files
and nothing else.

## The nine agents

One primary plus eight specialists (`role` must be one of the eight):

| Agent | Role | Owns |
|---|---|---|
| `port-orchestrator` | primary | the task graph, the merge, the report |
| `researcher` | researcher | external facts: engine, SDK, license, requirements |
| `reverse-engineer` | reverse-engineer | analysis: layout, symbols, assets, protection |
| `rust-engineer` | rust-engineer | Rust crates and the native runtime |
| `cpp-engineer` | cpp-engineer | C++ sources, CMake, the NDK build |
| `android-engineer` | android-engineer | manifest, Gradle, packaging, Java/Kotlin glue |
| `graphics-engineer` | graphics-engineer | rendering backend, shaders, asset conversion |
| `optimizer` | optimizer | profiling, memory, frame-time work |
| `qa-engineer` | qa-engineer | tests, device validation, evidence |

Definitions live in `.opencode/agents/*.md`.

## The contract

Every unit of work is one `TaskContract`: `id`, `role`, `objective`, `scope_in`,
`scope_out`, `inputs`, `outputs`, `owns`, `done_criteria`, `depends_on`. It is
strict on purpose — a unit with no done criterion cannot be verified, and a unit
that writes outside its `owns` claims breaks parallel work. Both are rejected at
parse time.

`render_prompt()` turns a contract into a complete sub-agent prompt. A sub-agent
does not see the orchestrator's conversation, so the prompt must stand alone.

## Scheduling and conflicts

- `pa agents graph --manifest file.json` validates the DAG (no cycles, no
  missing dependencies) and prints it.
- `pa agents plan --manifest file.json` returns the dispatchable set, honouring
  dependencies and a concurrency ceiling of **2 to 3**, decided by the RAM policy.
- Claim conflicts are detected pairwise. A conflicting unit **defers**; it is
  never forced. Two tasks with a dependency path between them may touch one file
  and are reported as a `sequential` handoff instead of a parallel conflict.
- Runs persist to a JSON store with atomic writes; `pa agents status` reads it,
  so a killed process can resume.

## Commands

```sh
pa agents list                          # the nine expected files and their state
pa agents validate                      # check agent files against the contract
pa agents graph --manifest m.json       # validate and print the DAG
pa agents plan  --manifest m.json       # next dispatch batch
pa agents status --run-id RUN           # inspect a run store
pa agents prompt --manifest m.json --id TASK
```

## No pinned model

Nothing forces a model or provider. Agents inherit the operator's selection;
`opencode.jsonc` deliberately carries no `model` field.

## Tests

`tests/test_agents.py` covers the contract validation (including the failure
cases), the conflict detector, the scheduler's concurrency ceiling, the graph,
the store's persistence and the agent registry.

```sh
python3 -m unittest tests.test_agents
```
