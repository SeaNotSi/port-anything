"""JSONL helpers with strict, streaming behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


class JsonlError(ValueError):
    """A JSONL file contains a malformed line."""


def iter_records(path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(line_number, object)`` for each non-empty line."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JsonlError(f"{file_path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise JsonlError(f"{file_path}:{number}: expected a JSON object")
            yield number, value


def read_records(path: str | Path) -> list[dict[str, Any]]:
    return [record for _, record in iter_records(path)]


def write_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            handle.write("\n")
    tmp.replace(target)


def canonical(record: dict[str, Any]) -> str:
    """Stable representation used for hashing and duplicate detection."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
