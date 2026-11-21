"""Zero-loss guard for context plans.

A plan is valid only when every line of every source is either present or
listed as an omitted range, every range is inside its file, and the token total
is within budget. The guard recomputes token costs rather than trusting the
plan's own numbers.
"""

from __future__ import annotations

from typing import Any

from ..util.hash import sha256_text
from .config import load as load_config
from .estimate import count_text
from .sources import SourceIndex


class LossDetected(RuntimeError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


def _text_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def check(plan: dict[str, Any], index: SourceIndex, budget: int | None = None) -> dict[str, Any]:
    cfg = load_config()
    violations: list[str] = []
    warnings: list[str] = []
    covered: dict[str, list[tuple[int, int, str]]] = {}
    hashes: dict[str, str] = {}
    recomputed = 0

    limit = int(plan.get("budget", 0) if budget is None else budget)
    for item in plan.get("items", []):
        source = index.get(item.get("source_id", ""))
        if source is None:
            violations.append(f"unknown source in items: {item.get('source_id')}")
            continue
        mode = item.get("mode")
        start = int(item.get("start_line", 0))
        end = int(item.get("end_line", 0))
        if mode == "alias":
            target = index.get(item.get("alias_of", ""))
            if target is None:
                violations.append(f"{source.id}: alias target missing")
            elif target.sha256 != source.sha256:
                violations.append(f"{source.id}: alias target hash differs")
            covered.setdefault(source.id, []).append((1, source.lines, "alias"))
            continue
        if mode == "index":
            continue
        if mode not in ("full", "window"):
            violations.append(f"{source.id}: unknown mode {mode!r}")
            continue
        if source.lines == 0:
            if start == 0 and end == 0:
                covered.setdefault(source.id, [])
                continue
            violations.append(f"{source.id}: zero-line source has a non-empty range")
            continue
        if start < 1 or end < start or end > source.lines:
            violations.append(f"{source.id}: range {start}-{end} outside 1-{source.lines}")
            continue
        expected = end - start + 1
        actual = _text_lines(item.get("text", ""))
        if actual != expected:
            violations.append(
                f"{source.id}: range {start}-{end} has {actual} lines of text, expected {expected}"
            )
        cost = count_text(item.get("text", ""), cfg["chars_per_token"], cfg["cjk_chars_per_token"]).tokens
        recomputed += cost
        if cost != int(item.get("tokens", -1)):
            violations.append(f"{source.id}: token count {item.get('tokens')} != recomputed {cost}")
        text_digest = sha256_text(item.get("text", ""))
        if text_digest in hashes:
            warnings.append(f"{source.id}: identical text already included by {hashes[text_digest]}")
        else:
            hashes[text_digest] = source.id
        covered.setdefault(source.id, []).append((start, end, mode))

    for gap in plan.get("omitted", []):
        source = index.get(gap.get("source_id", ""))
        if source is None:
            violations.append(f"unknown source in omitted: {gap.get('source_id')}")
            continue
        start = int(gap.get("start_line", 0))
        end = int(gap.get("end_line", 0))
        if start < 1 or end < start or end > source.lines:
            violations.append(f"{source.id}: omitted range {start}-{end} outside 1-{source.lines}")
            continue
        covered.setdefault(source.id, []).append((start, end, "omitted"))

    for source in index.files:
        if source.lines == 0:
            if source.id not in covered:
                violations.append(f"{source.id}: zero-line source is not accounted for")
            continue
        ranges = sorted(covered.get(source.id, []))
        if not ranges:
            violations.append(f"{source.id}: source is not accounted for at all")
            continue
        cursor = 1
        for start, end, kind in ranges:
            if start > cursor:
                violations.append(f"{source.id}: lines {cursor}-{start - 1} are unaccounted for")
            cursor = max(cursor, end + 1)
            if kind != "omitted":
                key = f"{source.id}:{start}-{end}"
                if key in hashes:
                    warnings.append(f"{source.id}: duplicate range {start}-{end}")
                hashes[key] = kind
        if cursor <= source.lines:
            violations.append(f"{source.id}: lines {cursor}-{source.lines} are unaccounted for")

    if recomputed > limit:
        violations.append(f"recomputed tokens {recomputed} exceed budget {limit}")
    if int(plan.get("total_tokens", 0)) != recomputed:
        violations.append(
            f"plan total_tokens {plan.get('total_tokens')} != recomputed {recomputed}"
        )
    if index.truncated:
        warnings.append("source index was truncated at max_files; increase max_files to index everything")

    return {
        "ok": not violations,
        "violations": violations,
        "warnings": warnings,
        "recomputed_tokens": recomputed,
        "budget": limit,
        "sources": len(index.files),
        "coverage": plan.get("coverage"),
    }


def assert_no_loss(plan: dict[str, Any], index: SourceIndex, budget: int | None = None) -> None:
    report = check(plan, index, budget)
    if not report["ok"]:
        raise LossDetected(report["violations"])
