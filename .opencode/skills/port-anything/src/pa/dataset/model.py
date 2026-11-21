"""PAKB record model and loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..util import jsonl
from ..util.paths import DATASETS_DIR, MANIFEST_FILE, RECORDS_DIR

CATEGORIES = (
    "engine",
    "platform",
    "graphics",
    "android",
    "rust",
    "cpp",
    "toolchain",
    "fileformat",
    "architecture",
    "buildsystem",
)

CATEGORY_SCHEMA = {
    "engine": "engine.schema.json",
    "platform": "platform.schema.json",
    "graphics": "graphics.schema.json",
    "toolchain": "toolchain.schema.json",
}


@dataclass
class Dataset:
    root: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    @property
    def by_id(self) -> dict[str, dict[str, Any]]:
        return {record["id"]: record for record in self.records}

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record["category"]] = counts.get(record["category"], 0) + 1
        return dict(sorted(counts.items()))

    def content_hash(self) -> str:
        digest = hashlib.sha256()
        for record in sorted(self.records, key=lambda r: r["id"]):
            digest.update(jsonl.canonical(record).encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()


def record_files(datasets_dir: str | Path = DATASETS_DIR) -> list[Path]:
    """Record files declared by the manifest, or every *.jsonl as a fallback."""
    root = Path(datasets_dir)
    manifest = root / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            declared = data.get("records")
            if isinstance(declared, list) and declared:
                files = []
                for entry in declared:
                    name = entry if isinstance(entry, str) else entry.get("file")
                    if not name:
                        continue
                    candidate = (root / "records" / name).resolve()
                    if candidate.is_file():
                        files.append(candidate)
                if files:
                    return sorted(files)
        except (json.JSONDecodeError, OSError):
            pass
    return sorted((root / "records").glob("*.jsonl"))


def load_dataset(datasets_dir: str | Path = DATASETS_DIR) -> Dataset:
    root = Path(datasets_dir)
    dataset = Dataset(root=root)
    for path in record_files(root):
        dataset.files.append(path)
        for number, record in jsonl.iter_records(path):
            record["__file"] = str(path.relative_to(root))
            record["__line"] = number
            dataset.records.append(record)
    return dataset


def strip_internal(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("__")}


def iter_public(records: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for record in records:
        yield strip_internal(record)


def dependency_graph(dataset: Dataset) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    known = set(dataset.by_id)
    for record in dataset.records:
        graph[record["id"]] = [dep for dep in record.get("dependencies", []) if dep in known]
    return graph


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Return one representative cycle per strongly connected component."""
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    cycles: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in graph.get(node, []):
            if neighbour not in indices:
                strongconnect(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], indices[neighbour])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph.get(node, []):
                cycles.append(sorted(component))

    for node in graph:
        if node not in indices:
            strongconnect(node)
    return cycles
