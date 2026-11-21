"""Dispatch scheduling.

Takes the DAG, the run state and a concurrency allowance, and returns the batch
that may be dispatched now. Three constraints apply, in order:

1. Dependencies — a task runs only when every upstream task is done.
2. File ownership — a task is not dispatched while a live task holds an
   overlapping claim. Two writers to one file is the failure mode this module
   exists to prevent.
3. Concurrency — capped at three. More specialists than that on a phone is
   slower and OOM-prone, and the third agent is rarely the bottleneck.

Everything deferred comes back with a reason, so the operator can see why a
ready task is not running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import conflict
from .contract import ContractError
from .graph import TaskGraph, owners_of
from .store import RunStore

MAX_CONCURRENT = 3
MIN_CONCURRENT = 1


@dataclass
class DispatchPlan:
    batch: list[str] = field(default_factory=list)
    deferred: dict[str, str] = field(default_factory=dict)
    concurrency: int = 1
    ready: list[str] = field(default_factory=list)
    conflicts: list[conflict.Conflict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch": list(self.batch),
            "deferred": dict(self.deferred),
            "concurrency": self.concurrency,
            "ready": list(self.ready),
            "conflicts": [item.as_dict() for item in self.conflicts],
        }


def clamp_concurrency(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("concurrency must be an integer")
    return max(MIN_CONCURRENT, min(MAX_CONCURRENT, value))


def plan(
    graph: TaskGraph,
    store: RunStore,
    *,
    concurrency: int = 2,
) -> DispatchPlan:
    limit = clamp_concurrency(concurrency)
    done = store.done_ids()
    running = store.running_ids()
    failed = store.failed_ids()

    ready = graph.ready(done, running, failed)
    plan_out = DispatchPlan(concurrency=limit, ready=ready)

    live_claims: list[tuple[str, tuple[str, ...]]] = []
    for task_id in running:
        entry = store.task(task_id)
        claims = tuple(str(item) for item in entry.get("owns") or ())
        if claims:
            live_claims.append((task_id, claims))

    selected: list[tuple[str, tuple[str, ...]]] = []
    for task_id in ready:
        if len(plan_out.batch) >= limit:
            plan_out.deferred[task_id] = f"concurrency limit ({limit})"
            continue
        contract = graph.tasks[task_id]
        claims = tuple(contract.owns)

        blocked = graph.blocked_by(task_id, done, failed)
        if blocked is not None:
            plan_out.deferred[task_id] = blocked
            continue

        if claims:
            clashes = conflict.check_dispatch([(task_id, claims)], live_claims)
            clashes += conflict.check_dispatch([(task_id, claims)], selected)
            if clashes:
                first = clashes[0]
                plan_out.conflicts.extend(clashes)
                other = first.task_b if first.task_a == task_id else first.task_a
                plan_out.deferred[task_id] = f"file conflict with {other} on {first.path_a}"
                continue

        plan_out.batch.append(task_id)
        selected.append((task_id, claims))

    for task_id in sorted(store.data["tasks"]):
        if task_id in plan_out.batch or task_id in plan_out.deferred:
            continue
        status = store.status(task_id)
        if status in ("pending",):
            plan_out.deferred[task_id] = (
                graph.blocked_by(task_id, done, failed) or "not scheduled"
            )
    return plan_out


def mark_dispatched(store: RunStore, batch: Iterable[str]) -> None:
    """Start the batch in the store and reserve its claims in one pass."""
    for task_id in batch:
        entry = store.start(task_id)
        claims = tuple(str(item) for item in entry.get("owns") or ())
        if claims:
            store.claim(task_id, claims)


def simulate(graph: TaskGraph, *, concurrency: int = 2) -> list[list[str]]:
    """Dry-run the whole schedule assuming every task succeeds.

    Used by ``pa agents plan --dry-run`` to show the shape of the run before a
    single sub-agent is dispatched. Raises if a batch cannot make progress.
    """
    limit = clamp_concurrency(concurrency)
    done: set[str] = set()
    batches: list[list[str]] = []
    guard = 0

    def claims_of(task_id: str) -> tuple[str, ...]:
        return tuple(graph.tasks[task_id].owns)

    while len(done) < len(graph):
        guard += 1
        if guard > len(graph) + 1:
            raise ContractError("scheduler stalled: no batch can make progress")
        ready = graph.ready(done)
        if not ready:
            raise ContractError(
                f"scheduler stalled with {len(graph) - len(done)} task(s) unfinished"
            )
        batch: list[str] = []
        selected: list[tuple[str, tuple[str, ...]]] = []
        for task_id in ready:
            if len(batch) >= limit:
                break
            claims = claims_of(task_id)
            if claims and conflict.check_dispatch([(task_id, claims)], selected):
                continue
            batch.append(task_id)
            selected.append((task_id, claims))
        if not batch:
            raise ContractError("no dispatchable task: every candidate is claim-blocked")
        batches.append(batch)
        done.update(batch)
    return batches


def pending_owners(graph: TaskGraph, store: RunStore) -> list[tuple[str, tuple[str, ...]]]:
    """Claims belonging to tasks that are neither done nor skipped."""
    owners: list[tuple[str, tuple[str, ...]]] = []
    for task_id, claims in owners_of(graph):
        if store.status(task_id) in ("done", "skipped"):
            continue
        owners.append((task_id, claims))
    return owners


def describe(plan_out: DispatchPlan) -> str:
    if not plan_out.batch:
        lines = ["nothing to dispatch"]
    else:
        lines = [f"dispatch {len(plan_out.batch)} of {plan_out.concurrency} slots:"]
        lines.extend(f"  {task_id}" for task_id in plan_out.batch)
    waiting = {
        task_id: reason
        for task_id, reason in plan_out.deferred.items()
        if reason != "not scheduled"
    }
    if waiting:
        lines.append("deferred:")
        for task_id in sorted(waiting):
            lines.append(f"  {task_id:<28} {waiting[task_id]}")
    return "\n".join(lines)