"""The shared task contract.

Every unit of work the orchestrator dispatches is one of these. The contract is
the only thing a sub-agent receives besides the repository, so it has to be
self-contained: objective, what is in and out of scope, what it may write, what
it must hand back, and how completion will be checked.

The contract is deliberately strict. A unit with no done criterion cannot be
verified, and a unit that writes outside its claims breaks parallel work for
everyone else — both are rejected here rather than discovered at merge time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from . import conflict

# The eight specialist roles. The orchestrator is not a task role: it dispatches
# and merges, it does not own a unit of porting work.
ROLES: tuple[str, ...] = (
    "researcher",
    "reverse-engineer",
    "rust-engineer",
    "cpp-engineer",
    "android-engineer",
    "graphics-engineer",
    "optimizer",
    "qa-engineer",
)

STATUSES = ("pending", "running", "done", "failed", "blocked", "skipped")

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ContractError(ValueError):
    """Raised when a task contract cannot be dispatched as written."""


@dataclass(frozen=True)
class TaskContract:
    id: str
    role: str
    objective: str
    scope_in: tuple[str, ...] = ()
    scope_out: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    done_criteria: tuple[str, ...] = ()
    owns: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskContract":
        if not isinstance(data, dict):
            raise ContractError("task contract must be a JSON object")
        missing = [key for key in ("id", "role", "objective") if not data.get(key)]
        if missing:
            raise ContractError(f"task contract missing required field(s): {', '.join(missing)}")
        contract = cls(
            id=str(data["id"]),
            role=str(data["role"]),
            objective=str(data["objective"]).strip(),
            scope_in=_strings(data.get("scope_in")),
            scope_out=_strings(data.get("scope_out")),
            inputs=_strings(data.get("inputs")),
            outputs=_strings(data.get("outputs")),
            done_criteria=_strings(data.get("done_criteria")),
            owns=_strings(data.get("owns")),
            depends_on=_strings(data.get("depends_on")),
            notes=str(data.get("notes") or "").strip(),
            metadata=dict(data.get("metadata") or {}),
        )
        contract.validate()
        return contract

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "objective": self.objective,
            "scope_in": list(self.scope_in),
            "scope_out": list(self.scope_out),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "done_criteria": list(self.done_criteria),
            "owns": list(self.owns),
            "depends_on": list(self.depends_on),
            "notes": self.notes,
            "metadata": dict(self.metadata),
        }

    def with_(self, **changes: Any) -> "TaskContract":
        return replace(self, **changes)

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        if not _ID.match(self.id):
            raise ContractError(
                f"task id {self.id!r} must be lowercase alphanumeric with . _ - (max 64 chars)"
            )
        if self.role not in ROLES:
            raise ContractError(
                f"task {self.id}: unknown role {self.role!r}; expected one of {', '.join(ROLES)}"
            )
        if len(self.objective) < 12:
            raise ContractError(f"task {self.id}: objective is too vague to dispatch")
        if len(self.objective) > 4000:
            raise ContractError(f"task {self.id}: objective exceeds 4000 characters")
        if not self.done_criteria:
            raise ContractError(f"task {self.id}: at least one done criterion is required")

        _reject_duplicates(self.id, "owns", self.owns)
        _reject_duplicates(self.id, "outputs", self.outputs)
        _reject_duplicates(self.id, "depends_on", self.depends_on)

        if self.id in self.depends_on:
            raise ContractError(f"task {self.id}: cannot depend on itself")

        for claim in self.owns:
            try:
                conflict.normalize(claim)
            except conflict.ClaimError as exc:
                raise ContractError(f"task {self.id}: {exc}") from exc
            if conflict.normalize(claim) != claim:
                raise ContractError(
                    f"task {self.id}: claim {claim!r} is not normalised "
                    f"(use {conflict.normalize(claim)!r})"
                )

        if self.outputs and not self.owns:
            raise ContractError(
                f"task {self.id}: produces {len(self.outputs)} output(s) but claims no files"
            )
        for output in self.outputs:
            try:
                covered = conflict.covers(self.owns, output)
            except conflict.ClaimError as exc:
                raise ContractError(f"task {self.id}: {exc}") from exc
            if not covered:
                raise ContractError(
                    f"task {self.id}: output {output!r} is outside its claims {list(self.owns)}"
                )

        in_and_out = set(self.scope_in) & set(self.scope_out)
        if in_and_out:
            raise ContractError(
                f"task {self.id}: path in both scope_in and scope_out: {sorted(in_and_out)}"
            )

    # ------------------------------------------------------------------ #
    # dispatch text
    # ------------------------------------------------------------------ #

    def render_prompt(self, *, run_id: str = "") -> str:
        """The complete prompt for one sub-agent. No conversation context needed."""
        lines = [
            f"# Task `{self.id}` — {self.role}",
        ]
        if run_id:
            lines.append(f"Run: `{run_id}`")
        lines.append("")
        lines.append("## Objective")
        lines.append(self.objective)
        lines.append("")
        lines.append("## In scope")
        lines.extend(f"- {item}" for item in (self.scope_in or ("nothing beyond the objective",)))
        lines.append("")
        lines.append("## Out of scope")
        lines.extend(f"- {item}" for item in (self.scope_out or ("everything not named above",)))
        lines.append("")
        lines.append("## Inputs to read")
        lines.extend(f"- `{item}`" for item in (self.inputs or ("none; work from the objective",)))
        lines.append("")
        lines.append("## Files you own")
        lines.extend(f"- `{item}`" for item in (self.owns or ("none — this task writes no files",)))
        if self.owns:
            lines.append("")
            lines.append("Do not modify anything outside these claims. If the task cannot")
            lines.append("be completed without touching another file, stop and report it")
            lines.append("as a blocker instead of widening the claim.")
        lines.append("")
        lines.append("## Deliverables")
        lines.extend(f"- `{item}`" for item in (self.outputs or ("a written finding, no file changes",)))
        lines.append("")
        lines.append("## Done when")
        lines.extend(f"- [ ] {item}" for item in self.done_criteria)
        if self.notes:
            lines.append("")
            lines.append("## Notes")
            lines.append(self.notes)
        lines.append("")
        lines.append("## Report back")
        lines.append("Return one JSON object and nothing else:")
        lines.append("")
        lines.append("```json")
        lines.append("{")
        lines.append(f'  "task_id": "{self.id}",')
        lines.append('  "status": "done | failed | blocked",')
        lines.append('  "files_changed": ["path"],')
        lines.append('  "commands_run": ["exact command"],')
        lines.append('  "evidence": "what you checked and what it printed",')
        lines.append('  "blockers": ["what stopped you"],')
        lines.append('  "notes": "anything the orchestrator must know"')
        lines.append("}")
        lines.append("```")
        lines.append("")
        lines.append("Report what actually happened. A command that was not run is not")
        lines.append("evidence, and an unverified claim is reported as unverified.")
        return "\n".join(lines)

    def claim_tuple(self) -> tuple[str, tuple[str, ...]]:
        return self.id, tuple(self.owns)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"expected a list of strings, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(f"expected non-empty strings, got {item!r}")
        out.append(item.strip())
    return tuple(out)


def _reject_duplicates(task_id: str, field_name: str, values: Sequence[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ContractError(f"task {task_id}: duplicate {field_name} entry {value!r}")
        seen.add(value)


def contracts_from_manifest(data: Any) -> list[TaskContract]:
    """Parse ``{"tasks": [...]}`` or a bare array."""
    if isinstance(data, dict):
        items: Iterable[Any] = data.get("tasks") or []
    elif isinstance(data, list):
        items = data
    else:
        raise ContractError("task manifest must be a JSON object or array")
    contracts = [TaskContract.from_dict(item) for item in items]
    ids = [contract.id for contract in contracts]
    duplicates = {item for item in ids if ids.count(item) > 1}
    if duplicates:
        raise ContractError(f"duplicate task ids: {sorted(duplicates)}")
    return contracts