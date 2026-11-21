"""One stage of a build, with the evidence that produced its state.

A stage is never "done because the next one produced a file". It is done when a
command ran, exited zero, and left the artifact it was supposed to leave. The
states are deliberately blunt:

``done``          ran and produced its output
``failed``        ran and did not
``unsupported``   could not run here; the missing dependency is named
``skipped``       not required by the chosen strategy
``pending``       not attempted yet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DONE = "done"
FAILED = "failed"
UNSUPPORTED = "unsupported"
SKIPPED = "skipped"
PENDING = "pending"

STATES = (DONE, FAILED, UNSUPPORTED, SKIPPED, PENDING)


@dataclass
class Stage:
    id: str
    state: str
    detail: str = ""
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    output: str = ""
    artifacts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown stage state {self.state!r}")

    @property
    def ok(self) -> bool:
        return self.state == DONE

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "detail": self.detail,
            "command": list(self.command),
            "returncode": self.returncode,
            "output": self.output,
            "artifacts": list(self.artifacts),
        }


def ok(stage_id: str, detail: str, *, command: list[str] | None = None, output: str = "", artifacts: list[str] | None = None) -> Stage:
    return Stage(stage_id, DONE, detail, list(command or []), 0, output, list(artifacts or []))


def fail(stage_id: str, detail: str, *, command: list[str] | None = None, returncode: int | None = None, output: str = "") -> Stage:
    return Stage(stage_id, FAILED, detail, list(command or []), returncode, output)


def unsupported(stage_id: str, detail: str, *, command: list[str] | None = None) -> Stage:
    return Stage(stage_id, UNSUPPORTED, detail, list(command or []), None, "")


def skipped(stage_id: str, detail: str = "") -> Stage:
    return Stage(stage_id, SKIPPED, detail)


def pending(stage_id: str) -> Stage:
    return Stage(stage_id, PENDING)


def from_result(stage_id: str, result: Any, *, detail: str = "", artifacts: list[str] | None = None) -> Stage:
    """Turn a :class:`pa.util.proc.Result` into a stage without losing output."""
    text = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    text = text.strip()
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated for the report; full output was not retained)"
    if result.ok:
        return Stage(
            stage_id,
            DONE,
            detail or "command exited 0",
            list(result.argv),
            result.returncode,
            text,
            list(artifacts or []),
        )
    return Stage(
        stage_id,
        FAILED,
        detail or result.summary(),
        list(result.argv),
        result.returncode,
        text,
    )


def summarise(stages: list[Stage]) -> dict[str, int]:
    counts = {state: 0 for state in STATES}
    for stage in stages:
        counts[stage.state] += 1
    return counts