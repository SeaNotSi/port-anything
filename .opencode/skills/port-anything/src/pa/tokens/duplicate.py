"""Duplicate and near-duplicate detection.

Deduplication removes repeated content, never unique content. Exact matches are
grouped by SHA-256. Near matches compare normalised line sets and are reported
with a similarity score so a human or agent can decide, rather than being
silently merged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..util.hash import sha256_text
from .config import load as load_config
from .sources import SourceIndex, index

WHITESPACE = re.compile(r"\s+")
NEAR_MAX_FILES = 200
NEAR_MAX_LINES = 4000
NEAR_MAX_BYTES = 2 * 1024 * 1024


def _normalised_lines(path: Path, limit: int) -> set[str]:
    lines: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle):
            if number >= limit:
                break
            text = WHITESPACE.sub(" ", line).strip().lower()
            if len(text) >= 8:
                lines.add(sha256_text(text)[:16])
    return lines


def find_duplicates(
    paths: Iterable[str | Path],
    root: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    source_index = index(paths, root=root, config=cfg)

    by_hash: dict[str, list[str]] = {}
    for source in source_index.files:
        by_hash.setdefault(source.sha256, []).append(source.id)

    exact = []
    savings = 0
    for digest, ids in sorted(by_hash.items()):
        if len(ids) < 2:
            continue
        canonical = source_index.get(ids[0])
        assert canonical is not None
        savings += canonical.tokens * (len(ids) - 1)
        exact.append(
            {
                "sha256": digest,
                "sources": [
                    {"id": source_id, "path": source_index.get(source_id).path}
                    for source_id in ids
                    if source_index.get(source_id) is not None
                ],
                "tokens_each": canonical.tokens,
                "tokens_saved": canonical.tokens * (len(ids) - 1),
            }
        )

    candidates = [
        source
        for source in source_index.files
        if source.bytes <= NEAR_MAX_BYTES and source.lines <= NEAR_MAX_LINES
    ][:NEAR_MAX_FILES]
    line_sets: dict[str, set[str]] = {}
    for source in candidates:
        try:
            line_sets[source.id] = _normalised_lines(source_index.abs_path(source), NEAR_MAX_LINES)
        except OSError:
            continue

    threshold = float(cfg.get("duplicate_near_threshold", 0.85))
    near: list[dict[str, Any]] = []
    ids = [source.id for source in candidates if source.id in line_sets]
    for left in range(len(ids)):
        for right in range(left + 1, len(ids)):
            a = line_sets[ids[left]]
            b = line_sets[ids[right]]
            if not a or not b:
                continue
            union = len(a | b)
            if not union:
                continue
            similarity = len(a & b) / union
            if similarity >= threshold:
                near.append(
                    {
                        "a": ids[left],
                        "b": ids[right],
                        "path_a": source_index.get(ids[left]).path,
                        "path_b": source_index.get(ids[right]).path,
                        "similarity": round(similarity, 4),
                    }
                )

    near.sort(key=lambda entry: (-entry["similarity"], entry["a"], entry["b"]))
    return {
        "exact": exact,
        "near": near,
        "savings_tokens": savings,
        "files": len(source_index.files),
        "skipped": source_index.skipped,
        "threshold": threshold,
    }


def duplicate_groups(source_index: SourceIndex) -> dict[str, list[str]]:
    """Return hash -> source ids for exact duplicates only."""
    groups: dict[str, list[str]] = {}
    for source in source_index.files:
        groups.setdefault(source.sha256, []).append(source.id)
    return {digest: ids for digest, ids in groups.items() if len(ids) > 1}
