"""Lossless context planning.

The planner fills a token budget without deleting source. Every file is one of:

``full``    the whole file is present;
``window``  selected line ranges are present, and every other line is listed as
            an omitted range that can be retrieved later;
``alias``   an exact duplicate of another source, referenced instead of repeated;
``index``   only metadata is present because nothing fit, with all lines listed
            as omitted.

The union of included and omitted ranges always covers every line of every
source. :mod:`pa.tokens.guard` enforces that contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .config import load as load_config
from .estimate import count_text
from .sources import SourceFile, SourceIndex

WORD = re.compile(r"[A-Za-z0-9_]{2,}")


@dataclass
class _Range:
    start: int
    end: int

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


def _merge(ranges: Iterable[_Range], total: int) -> list[_Range]:
    ordered = sorted(
        (_Range(max(1, item.start), min(total, item.end)) for item in ranges if item.end >= item.start),
        key=lambda item: (item.start, item.end),
    )
    merged: list[_Range] = []
    for item in ordered:
        if merged and item.start <= merged[-1].end + 1:
            merged[-1].end = max(merged[-1].end, item.end)
        else:
            merged.append(_Range(item.start, item.end))
    return merged


def _complement(included: list[_Range], total: int) -> list[_Range]:
    gaps: list[_Range] = []
    cursor = 1
    for item in included:
        if item.start > cursor:
            gaps.append(_Range(cursor, item.start - 1))
        cursor = max(cursor, item.end + 1)
    if cursor <= total:
        gaps.append(_Range(cursor, total))
    return gaps


def _terms(query: str) -> list[str]:
    return sorted({match.group(0).lower() for match in WORD.finditer(query or "")})


def _score(source: SourceFile, index: SourceIndex, terms: list[str], must_keep: set[str]) -> int:
    score = 0
    if source.id in must_keep or source.path in must_keep:
        score += 1000
    lowered_path = source.path.lower()
    for term in terms:
        if term in lowered_path:
            score += 20
    if terms:
        try:
            with index.abs_path(source).open("r", encoding="utf-8", errors="replace") as handle:
                sample = handle.read(8192).lower()
        except OSError:
            sample = ""
        for term in terms:
            score += min(sample.count(term), 10)
    return score


def _window_ranges(
    source: SourceFile,
    index: SourceIndex,
    terms: list[str],
    head_lines: int,
    tail_lines: int,
    context: int = 3,
    max_hits: int = 6,
) -> list[_Range]:
    total = source.lines
    if total == 0:
        return []
    ranges = [
        _Range(1, min(total, head_lines)),
        _Range(max(1, total - tail_lines + 1), total),
    ]
    if terms:
        try:
            with index.abs_path(source).open("r", encoding="utf-8", errors="replace") as handle:
                hits = 0
                for number, line in enumerate(handle, start=1):
                    lowered = line.lower()
                    if any(term in lowered for term in terms):
                        ranges.append(
                            _Range(max(1, number - context), min(total, number + context))
                        )
                        hits += 1
                        if hits >= max_hits:
                            break
        except OSError:
            pass
    return _merge(ranges, total)


def _read_lines(index: SourceIndex, source: SourceFile, item: _Range) -> str:
    return index.read_slice(source.id, item.start, item.end)


def plan(
    index: SourceIndex,
    budget: int | None = None,
    query: str = "",
    must_keep: Iterable[str] = (),
    config: dict[str, Any] | None = None,
    dedupe: bool = True,
) -> dict[str, Any]:
    """Build a budgeted, lossless context plan for *index*."""
    cfg = config or load_config()
    if budget is None:
        budget = int(cfg["context_default_budget"])
    budget = max(0, int(budget))
    terms = _terms(query)
    keep = set(must_keep)

    order = sorted(
        index.files,
        key=lambda source: (
            -_score(source, index, terms, keep),
            source.tokens,
            source.path,
        ),
    )

    seen_hashes: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    used = 0

    for source in order:
        if dedupe and source.sha256 in seen_hashes:
            items.append(
                {
                    "source_id": source.id,
                    "path": source.path,
                    "mode": "alias",
                    "alias_of": seen_hashes[source.sha256],
                    "start_line": 1,
                    "end_line": source.lines,
                    "tokens": 0,
                    "text": "",
                }
            )
            continue
        seen_hashes[source.sha256] = source.id

        if source.lines == 0:
            items.append(
                {
                    "source_id": source.id,
                    "path": source.path,
                    "mode": "full",
                    "start_line": 0,
                    "end_line": 0,
                    "tokens": 0,
                    "text": "",
                }
            )
            continue

        if used + source.tokens <= budget:
            text = index.read_slice(source.id, 1, source.lines)
            cost = count_text(text, cfg["chars_per_token"], cfg["cjk_chars_per_token"]).tokens
            if used + cost <= budget:
                items.append(
                    {
                        "source_id": source.id,
                        "path": source.path,
                        "mode": "full",
                        "start_line": 1,
                        "end_line": source.lines,
                        "tokens": cost,
                        "text": text,
                    }
                )
                used += cost
                continue

        windows = _window_ranges(
            source,
            index,
            terms,
            int(cfg["head_lines"]),
            int(cfg["tail_lines"]),
        )
        included: list[_Range] = []
        for window in windows:
            if window.lines <= 0:
                continue
            text = _read_lines(index, source, window)
            cost = count_text(text, cfg["chars_per_token"], cfg["cjk_chars_per_token"]).tokens
            if used + cost > budget:
                continue
            items.append(
                {
                    "source_id": source.id,
                    "path": source.path,
                    "mode": "window",
                    "start_line": window.start,
                    "end_line": window.end,
                    "tokens": cost,
                    "text": text,
                }
            )
            used += cost
            included.append(window)

        included = _merge(included, source.lines)
        if not included:
            items.append(
                {
                    "source_id": source.id,
                    "path": source.path,
                    "mode": "index",
                    "start_line": 0,
                    "end_line": 0,
                    "tokens": 0,
                    "text": "",
                }
            )
        for gap in _complement(included, source.lines):
            omitted.append(
                {
                    "source_id": source.id,
                    "path": source.path,
                    "start_line": gap.start,
                    "end_line": gap.end,
                    "reason": "budget",
                }
            )

    included_lines = sum(
        item["end_line"] - item["start_line"] + 1
        for item in items
        if item["mode"] in ("full", "window", "alias")
    )
    total_lines = index.total_lines
    coverage = (included_lines / total_lines) if total_lines else 1.0

    return {
        "budget": budget,
        "total_tokens": used,
        "coverage": round(coverage, 4),
        "included_lines": included_lines,
        "total_lines": total_lines,
        "items": items,
        "omitted": omitted,
        "sources": len(index.files),
        "query": query,
        "must_keep": sorted(keep),
        "method": "range-index-v1",
    }


def render_plan(plan_dict: dict[str, Any]) -> str:
    """Render a plan as the text an agent receives."""
    lines: list[str] = []
    lines.append(
        f"# Context plan — budget {plan_dict['budget']}, used {plan_dict['total_tokens']}, "
        f"coverage {plan_dict['coverage'] * 100:.1f}%"
    )
    lines.append("")
    for item in plan_dict["items"]:
        if item["mode"] == "alias":
            lines.append(
                f"## {item['source_id']} {item['path']} "
                f"[alias of {item['alias_of']}, lines 1-{item['end_line']}]"
            )
            lines.append("")
            continue
        if item["mode"] == "index":
            lines.append(f"## {item['source_id']} {item['path']} [index only — content omitted]")
            lines.append("")
            continue
        label = "full" if item["mode"] == "full" else f"lines {item['start_line']}-{item['end_line']}"
        span = f"lines 1-{item['end_line']}" if item["mode"] == "full" else label
        lines.append(f"## {item['source_id']} {item['path']} [{span}]")
        lines.append("")
        lines.append(item["text"].rstrip("\n"))
        lines.append("")
    if plan_dict["omitted"]:
        lines.append("## Omitted ranges (retrieve with `pa tokens slice`)")
        lines.append("")
        for gap in plan_dict["omitted"]:
            lines.append(
                f"- {gap['source_id']} {gap['path']} lines {gap['start_line']}-{gap['end_line']} "
                f"({gap['reason']})"
            )
    return "\n".join(lines).rstrip() + "\n"
