"""Persistent run state for a porting session.

State lives in one JSON file so a run can be resumed after the device kills the
process, the connection drops, or the operator closes the terminal. Writes are
atomic — temp file in the same directory, fsync, rename — so a crash mid-save
leaves the previous state intact instead of a truncated one.

Task status moves in one direction: ``pending -> running -> done|failed``, with
``blocked`` and ``skipped`` as terminal alternatives. Illegal transitions raise
instead of being silently accepted, because a resumed run must never believe a
task finished when it did not.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from .contract import STATUSES, TaskContract
from .graph import TaskGraph

STATE_VERSION = 1
TERMINAL = frozenset({"done", "failed", "blocked", "skipped"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "skipped", "blocked"}),
    "running": frozenset({"done", "failed", "blocked"}),
    "failed": frozenset({"pending", "running"}),
    "blocked": frozenset({"pending", "running", "skipped"}),
    "done": frozenset(),
    "skipped": frozenset(),
}


class StateError(ValueError):
    """Raised for an illegal transition or an unusable state file."""


class ClaimConflict(StateError):
    """Raised when two live tasks claim an overlapping file."""


def _now() -> float:
    return round(time.time(), 3)


class RunStore:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        graph: TaskGraph,
        path: str | Path,
        *,
        run_id: str | None = None,
        resume: bool = True,
    ) -> "RunStore":
        target = Path(path).expanduser()
        if target.is_file() and resume:
            return cls.load(target, graph)
        stamp = _now()
        data: dict[str, Any] = {
            "version": STATE_VERSION,
            "run": run_id or f"run-{int(stamp)}",
            "created": stamp,
            "updated": stamp,
            "tasks": {},
            "claims": {},
        }
        for contract in graph.tasks.values():
            data["tasks"][contract.id] = {
                "status": "pending",
                "role": contract.role,
                "depends_on": list(contract.depends_on),
                "owns": list(contract.owns),
                "attempts": 0,
                "started": None,
                "finished": None,
                "result": None,
                "error": "",
            }
        return cls(target, data)

    @classmethod
    def load(cls, path: str | Path, graph: TaskGraph | None = None) -> "RunStore":
        target = Path(path).expanduser()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StateError(f"cannot read state file {target}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise StateError(f"state file {target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise StateError(f"state file {target} has an unsupported version")
        if not isinstance(data.get("tasks"), dict):
            raise StateError(f"state file {target} has no task table")
        unknown = set(data["tasks"]) - set(graph.tasks) if graph else set()
        if unknown:
            raise StateError(f"state file has tasks not in the graph: {sorted(unknown)}")
        if graph:
            for task_id in graph.tasks:
                data["tasks"].setdefault(
                    task_id,
                    {
                        "status": "pending",
                        "role": graph.tasks[task_id].role,
                        "depends_on": list(graph.tasks[task_id].depends_on),
                        "owns": list(graph.tasks[task_id].owns),
                        "attempts": 0,
                        "started": None,
                        "finished": None,
                        "result": None,
                        "error": "",
                    },
                )
        store = cls(target, data)
        store.data.setdefault("claims", {})
        for task_id, entry in store.data["tasks"].items():
            if entry.get("status") not in STATUSES:
                raise StateError(f"task {task_id} has unknown status {entry.get('status')!r}")
        return store

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated"] = _now()
        payload = json.dumps(self.data, indent=2, sort_keys=False).encode("utf-8")
        handle, tmp_name = tempfile.mkstemp(prefix=".state-", dir=str(target.parent))
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self.path = target
        return target

    # ------------------------------------------------------------------ #
    # transitions
    # ------------------------------------------------------------------ #

    def status(self, task_id: str) -> str:
        return str(self.task(task_id)["status"])

    def task(self, task_id: str) -> dict[str, Any]:
        try:
            return self.data["tasks"][task_id]
        except KeyError as exc:
            raise StateError(f"unknown task {task_id!r}") from exc

    def _move(self, task_id: str, target: str) -> dict[str, Any]:
        entry = self.task(task_id)
        current = str(entry["status"])
        if target not in _TRANSITIONS.get(current, frozenset()):
            raise StateError(f"task {task_id}: illegal transition {current} -> {target}")
        entry["status"] = target
        return entry

    def start(self, task_id: str, *, now: float | None = None) -> dict[str, Any]:
        entry = self._move(task_id, "running")
        entry["started"] = now if now is not None else _now()
        entry["finished"] = None
        entry["error"] = ""
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        return entry

    def finish(
        self,
        task_id: str,
        *,
        result: Any = None,
        ok: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        entry = self._move(task_id, "done" if ok else "failed")
        entry["finished"] = now if now is not None else _now()
        entry["result"] = result
        if not ok:
            entry["error"] = str((result or {}).get("error", "") if isinstance(result, dict) else result or "")
        self.release(task_id)
        return entry

    def block(self, task_id: str, reason: str) -> dict[str, Any]:
        entry = self._move(task_id, "blocked")
        entry["error"] = reason
        entry["finished"] = _now()
        self.release(task_id)
        return entry

    def skip(self, task_id: str, reason: str = "") -> dict[str, Any]:
        entry = self._move(task_id, "skipped")
        entry["error"] = reason
        entry["finished"] = _now()
        self.release(task_id)
        return entry

    def retry(self, task_id: str) -> dict[str, Any]:
        if self.status(task_id) not in ("failed", "blocked"):
            raise StateError(
                f"task {task_id}: only failed or blocked tasks can be retried "
                f"(current: {self.status(task_id)})"
            )
        entry = self._move(task_id, "pending")
        entry["error"] = ""
        entry["finished"] = None
        return entry

    # ------------------------------------------------------------------ #
    # claims
    # ------------------------------------------------------------------ #

    def claims(self) -> dict[str, str]:
        return {str(path): str(task_id) for path, task_id in self.data.get("claims", {}).items()}

    def claim(self, task_id: str, patterns: Iterable[str], *, force: bool = False) -> dict[str, str]:
        """Reserve files for *task_id*; overlapping live claims raise."""
        from . import conflict

        existing = self.claims()
        wanted = list(patterns)
        for pattern in wanted:
            for held_path, holder in existing.items():
                if holder == task_id:
                    continue
                if not self._live(holder):
                    continue
                if conflict.overlaps(pattern, held_path):
                    if force:
                        continue
                    raise ClaimConflict(
                        f"task {task_id} claim {pattern!r} overlaps {held_path!r} "
                        f"held by live task {holder}"
                    )
        for pattern in wanted:
            existing[pattern] = task_id
        self.data["claims"] = existing
        return existing

    def _live(self, task_id: str) -> bool:
        entry = self.data["tasks"].get(task_id)
        return bool(entry) and entry.get("status") == "running"

    def release(self, task_id: str) -> dict[str, str]:
        existing = {
            path: holder
            for path, holder in self.claims().items()
            if holder != task_id
        }
        self.data["claims"] = existing
        return existing

    def release_stale(self, *, ttl_seconds: float, now: float | None = None) -> list[str]:
        """Mark running tasks older than *ttl* as failed and free their claims."""
        moment = now if now is not None else _now()
        released: list[str] = []
        for task_id, entry in self.data["tasks"].items():
            if entry.get("status") != "running":
                continue
            started = entry.get("started")
            if not isinstance(started, (int, float)):
                continue
            if moment - started < ttl_seconds:
                continue
            entry["status"] = "failed"
            entry["finished"] = moment
            entry["error"] = f"stale: running longer than {ttl_seconds:.0f}s"
            self.release(task_id)
            released.append(task_id)
        return released

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #

    def done_ids(self) -> set[str]:
        return {
            task_id
            for task_id, entry in self.data["tasks"].items()
            if entry.get("status") == "done"
        }

    def running_ids(self) -> set[str]:
        return {
            task_id
            for task_id, entry in self.data["tasks"].items()
            if entry.get("status") == "running"
        }

    def failed_ids(self) -> set[str]:
        return {
            task_id
            for task_id, entry in self.data["tasks"].items()
            if entry.get("status") in ("failed", "blocked")
        }

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        for entry in self.data["tasks"].values():
            status = str(entry.get("status"))
            if status in counts:
                counts[status] += 1
        return counts

    def finished(self) -> bool:
        return all(
            entry.get("status") in TERMINAL for entry in self.data["tasks"].values()
        )

    def summary(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "run": self.data.get("run"),
            "path": str(self.path),
            "tasks": len(self.data["tasks"]),
            "counts": counts,
            "claims": len(self.claims()),
            "finished": self.finished(),
            "updated": self.data.get("updated"),
        }