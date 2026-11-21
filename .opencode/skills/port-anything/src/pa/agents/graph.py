"""Task DAG over shared contracts.

The graph is the schedule. Nodes are contracts, edges are dependencies, and a
layer is every task that can run once the previous layer finished. Cycles are
rejected before dispatch because a cycle is a specification bug, not something
to resolve at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .contract import ContractError, TaskContract


@dataclass
class TaskGraph:
    tasks: dict[str, TaskContract] = field(default_factory=dict)

    @classmethod
    def from_contracts(cls, contracts: Iterable[TaskContract]) -> "TaskGraph":
        graph = cls()
        for contract in contracts:
            if contract.id in graph.tasks:
                raise ContractError(f"duplicate task id {contract.id!r}")
            graph.tasks[contract.id] = contract
        problems = graph.problems()
        if problems:
            raise ContractError("; ".join(problems))
        return graph

    # ------------------------------------------------------------------ #
    # structure
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self.tasks)

    def ids(self) -> list[str]:
        return sorted(self.tasks)

    def dependencies(self, task_id: str) -> tuple[str, ...]:
        return self.tasks[task_id].depends_on

    def dependents(self, task_id: str) -> list[str]:
        return sorted(
            other.id for other in self.tasks.values() if task_id in other.depends_on
        )

    def problems(self) -> list[str]:
        """Unknown dependencies and cycles, in that order."""
        found: list[str] = []
        for contract in self.tasks.values():
            for dependency in contract.depends_on:
                if dependency not in self.tasks:
                    found.append(f"task {contract.id}: unknown dependency {dependency!r}")
        if not found:
            for cycle in self.cycles():
                found.append(f"dependency cycle: {' -> '.join(cycle)}")
        return found

    def validate(self) -> None:
        problems = self.problems()
        if problems:
            raise ContractError("; ".join(problems))

    # ------------------------------------------------------------------ #
    # ordering
    # ------------------------------------------------------------------ #

    def cycles(self) -> list[list[str]]:
        """Cycles found by Kahn's algorithm, each rendered as a closed path."""
        indegree = {task_id: len(self.dependencies(task_id)) for task_id in self.tasks}
        queue = sorted(task_id for task_id, degree in indegree.items() if degree == 0)
        removed: list[str] = []
        while queue:
            current = queue.pop(0)
            removed.append(current)
            for dependent in self.dependents(current):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
                    queue.sort()
        leftover = {task_id for task_id, degree in indegree.items() if degree > 0}
        if not leftover:
            return []
        cycles: list[list[str]] = []
        seen: set[str] = set()
        for start in sorted(leftover):
            if start in seen:
                continue
            path: list[str] = []
            current = start
            while current not in path:
                path.append(current)
                candidates = [dep for dep in self.dependencies(current) if dep in leftover]
                if not candidates:
                    break
                current = sorted(candidates)[0]
            if current in path:
                loop = path[path.index(current) :]
                cycles.append(loop + [current])
                seen.update(loop)
        return cycles

    def topo_order(self) -> list[str]:
        """Deterministic dependency order."""
        if self.problems():
            raise ContractError("; ".join(self.problems()))
        indegree = {task_id: len(self.dependencies(task_id)) for task_id in self.tasks}
        queue = sorted(task_id for task_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for dependent in self.dependents(current):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
            queue.sort()
        return order

    def layers(self) -> list[list[str]]:
        """Every task grouped by dependency depth; a layer can run in parallel."""
        if self.problems():
            raise ContractError("; ".join(self.problems()))
        depth: dict[str, int] = {}

        def resolve(task_id: str) -> int:
            if task_id in depth:
                return depth[task_id]
            deps = self.dependencies(task_id)
            value = 0 if not deps else 1 + max(resolve(dep) for dep in deps)
            depth[task_id] = value
            return value

        for task_id in self.tasks:
            resolve(task_id)
        grouped: dict[int, list[str]] = {}
        for task_id, level in depth.items():
            grouped.setdefault(level, []).append(task_id)
        return [sorted(grouped[level]) for level in sorted(grouped)]

    def related(self, first: str, second: str) -> bool:
        """True when one task depends on the other, directly or transitively."""
        return second in self.downstream(first) or first in self.downstream(second)

    def downstream(self, task_id: str) -> set[str]:
        found: set[str] = set()
        stack = list(self.dependents(task_id))
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(self.dependents(current))
        return found

    # ------------------------------------------------------------------ #
    # readiness
    # ------------------------------------------------------------------ #

    def ready(
        self,
        done: Iterable[str] = (),
        running: Iterable[str] = (),
        failed: Iterable[str] = (),
    ) -> list[str]:
        """Tasks whose dependencies are all done and that are not in flight."""
        finished = set(done)
        busy = set(running) | set(failed)
        return sorted(
            task_id
            for task_id, contract in self.tasks.items()
            if task_id not in finished
            and task_id not in busy
            and all(dep in finished for dep in contract.depends_on)
        )

    def blocked_by(self, task_id: str, done: Iterable[str] = (), failed: Iterable[str] = ()) -> str | None:
        """Human reason a ready task is blocked, or ``None`` when it can run."""
        finished = set(done)
        broken = set(failed)
        unsatisfied = [dep for dep in self.dependencies(task_id) if dep not in finished]
        if not unsatisfied:
            return None
        hard = [dep for dep in unsatisfied if dep in broken]
        if hard:
            return f"upstream task failed: {', '.join(sorted(hard))}"
        return f"waiting on: {', '.join(sorted(unsatisfied))}"

    def roots(self) -> list[str]:
        return sorted(task_id for task_id in self.tasks if not self.dependencies(task_id))

    def leaves(self) -> list[str]:
        return sorted(task_id for task_id in self.tasks if not self.dependents(task_id))


def owners_of(graph: TaskGraph | Sequence[TaskContract]) -> list[tuple[str, tuple[str, ...]]]:
    contracts = graph.tasks.values() if isinstance(graph, TaskGraph) else graph
    return [(contract.id, tuple(contract.owns)) for contract in contracts if contract.owns]