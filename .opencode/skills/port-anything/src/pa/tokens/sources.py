"""Source indexing with hashes and line ranges.

A source is never replaced by a summary. The index keeps the file path, its
SHA-256, its line count and its token cost; context planning then references
line ranges inside that file. Binary files are listed as skipped with a reason
rather than silently ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..util.hash import sha256_bytes, sha256_file
from . import cache
from .config import load as load_config
from .estimate import estimate_file

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "target", "build", "dist", "out",
    "obj", "Binaries", "Intermediate", "DerivedDataCache", "Library", "Temp",
    ".gradle", ".idea", ".vs", "__pycache__", ".venv", "venv", "vendor",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
}

LANGUAGES = {
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".rs": "rust", ".cs": "csharp", ".gd": "gdscript", ".lua": "lua", ".py": "python",
    ".rpy": "renpy", ".java": "java", ".kt": "kotlin", ".js": "javascript",
    ".ts": "typescript", ".go": "go", ".sh": "shell", ".json": "json", ".jsonl": "jsonl",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".md": "markdown", ".xml": "xml",
    ".gradle": "gradle", ".cmake": "cmake", ".glsl": "glsl", ".hlsl": "hlsl",
    ".wgsl": "wgsl", ".sql": "sql", ".html": "html", ".css": "css",
}

TEXT_PROBE = 4096


@dataclass
class SourceFile:
    id: str
    path: str
    bytes: int
    lines: int
    sha256: str
    language: str
    tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "bytes": self.bytes,
            "lines": self.lines,
            "sha256": self.sha256,
            "language": self.language,
            "tokens": self.tokens,
        }


@dataclass
class SourceIndex:
    root: str
    files: list[SourceFile] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False

    def __post_init__(self) -> None:
        self._by_id = {source.id: source for source in self.files}

    def get(self, source_id: str) -> SourceFile | None:
        return self._by_id.get(source_id)

    def by_path(self, path: str) -> SourceFile | None:
        for source in self.files:
            if source.path == path:
                return source
        return None

    @property
    def total_tokens(self) -> int:
        return sum(source.tokens for source in self.files)

    @property
    def total_lines(self) -> int:
        return sum(source.lines for source in self.files)

    @property
    def total_bytes(self) -> int:
        return sum(source.bytes for source in self.files)

    def manifest_hash(self) -> str:
        payload = json.dumps(
            [source.as_dict() for source in self.files],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256_bytes(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files": [source.as_dict() for source in self.files],
            "skipped": self.skipped,
            "truncated": self.truncated,
            "totals": {
                "files": len(self.files),
                "lines": self.total_lines,
                "bytes": self.total_bytes,
                "tokens": self.total_tokens,
            },
            "manifest_sha256": self.manifest_hash(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceIndex":
        files = [SourceFile(**entry) for entry in data.get("files", [])]
        return cls(
            root=str(data.get("root", "")),
            files=files,
            skipped=list(data.get("skipped", [])),
            truncated=bool(data.get("truncated", False)),
        )

    def abs_path(self, source: SourceFile) -> Path:
        return Path(self.root) / source.path

    def read_slice(self, source_id: str, start: int, end: int) -> str:
        source = self.get(source_id)
        if source is None:
            raise KeyError(f"unknown source: {source_id}")
        if start < 1 or end < start or end > source.lines:
            raise ValueError(
                f"range {start}-{end} outside {source.path} (1-{source.lines})"
            )
        lines: list[str] = []
        with self.abs_path(source).open("r", encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                if number < start:
                    continue
                if number > end:
                    break
                lines.append(line)
        return "".join(lines)

    def verify(self, source_id: str) -> bool:
        source = self.get(source_id)
        if source is None:
            return False
        return sha256_file(self.abs_path(source)) == source.sha256

    def search(self, query: str, limit: int = 20, context_lines: int = 2) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        needle = query.lower()
        hits: list[dict[str, Any]] = []
        for source in self.files:
            path = self.abs_path(source)
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, start=1):
                        if needle in line.lower():
                            hits.append(
                                {
                                    "source_id": source.id,
                                    "path": source.path,
                                    "line": number,
                                    "start_line": max(1, number - context_lines),
                                    "end_line": min(source.lines, number + context_lines),
                                    "preview": line.strip()[:200],
                                }
                            )
                            if len(hits) >= limit:
                                return hits
            except OSError:
                continue
        return hits


def _language(path: Path) -> str:
    if path.name == "CMakeLists.txt":
        return "cmake"
    return LANGUAGES.get(path.suffix.lower(), "text")


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(TEXT_PROBE)
    except OSError:
        return True


def _expand(paths: Iterable[Path]) -> tuple[list[Path], list[dict[str, str]]]:
    found: list[Path] = []
    skipped: list[dict[str, str]] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
            continue
        if not path.is_dir():
            skipped.append({"path": str(path), "reason": "missing"})
            continue
        for candidate in path.rglob("*"):
            if candidate.is_dir():
                continue
            try:
                relative = candidate.relative_to(path)
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in relative.parts[:-1]):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(resolved)
    return found, skipped


def _common_root(entries: list[Path]) -> Path:
    parents: list[Path] = []
    for entry in entries:
        if entry.is_file() or not entry.exists():
            parents.append(entry.resolve().parent)
        else:
            parents.append(entry.resolve())
    if not parents:
        return Path.cwd()
    if len(parents) == 1:
        return parents[0]
    try:
        return Path(os.path.commonpath([str(parent) for parent in parents]))
    except ValueError:
        return Path.cwd()


def expand_paths(paths: Iterable[str | Path]) -> tuple[list[Path], list[dict[str, str]]]:
    """Public wrapper: expand files and directories into text-candidate paths."""
    return _expand([Path(entry).expanduser() for entry in paths])


def index(
    paths: Iterable[str | Path],
    root: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> SourceIndex:
    cfg = config or load_config()
    entries = [Path(entry).expanduser() for entry in paths]
    files, skipped = _expand(entries)
    if root is not None:
        root_path = Path(root).expanduser().resolve()
    else:
        root_path = _common_root(entries)

    max_files = int(cfg["max_files"])
    truncated = len(files) > max_files
    if truncated:
        files = files[:max_files]

    use_cache = bool(cfg.get("cache_enabled", True))
    sources: list[SourceFile] = []
    for path in sorted(files, key=lambda item: str(item)):
        try:
            relative = str(path.relative_to(root_path))
        except ValueError:
            relative = str(path)
        if _is_binary(path):
            skipped.append({"path": relative, "reason": "binary"})
            continue
        size = path.stat().st_size
        digest = sha256_file(path)
        key = f"{size}:{digest}:{cfg['chars_per_token']}:{cfg['cjk_chars_per_token']}"
        estimate = cache.get("estimate", key) if use_cache else None
        if estimate is None:
            estimate = estimate_file(path, cfg)
            if use_cache:
                cache.put("estimate", key, estimate)
        sources.append(
            SourceFile(
                id=f"src-{len(sources) + 1:04d}",
                path=relative,
                bytes=size,
                lines=int(estimate.get("lines", 0)),
                sha256=digest,
                language=_language(path),
                tokens=int(estimate.get("tokens", 0)),
            )
        )

    return SourceIndex(root=str(root_path), files=sources, skipped=skipped, truncated=truncated)


MANIFEST_KIND = "pa-source-index"
MANIFEST_VERSION = 1


def save_manifest(source_index: SourceIndex, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kind": MANIFEST_KIND, "version": MANIFEST_VERSION, **source_index.to_dict()}
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def load_manifest(path: str | Path) -> SourceIndex:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("kind") != MANIFEST_KIND:
        raise ValueError(f"not a source manifest: {path}")
    if int(data.get("version", 0)) > MANIFEST_VERSION:
        raise ValueError(f"manifest version {data.get('version')} is newer than this tool")
    return SourceIndex.from_dict(data)
