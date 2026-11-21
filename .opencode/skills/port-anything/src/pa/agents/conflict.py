"""File-claim overlap detection.

Two specialists must never write the same file in the same dispatch batch. This
module decides whether two claim patterns can touch the same path.

The detector is deliberately conservative. A false positive serialises two units
that could have run together — a little time lost. A false negative lets two
agents write one file — work lost. When a pair is genuinely undecidable (two
wildcards over the same directory), it is reported as a conflict and the
orchestrator runs them in sequence.

Path grammar: POSIX, relative, forward slashes. ``*`` matches inside one path
segment, ``**`` crosses segments, ``?`` matches one non-slash character, and
brackets are character classes. A trailing directory name claims everything
under it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

WILDCARD = re.compile(r"[*?\[]")
_ABSOLUTE = re.compile(r"^(/|[A-Za-z]:)")


class ClaimError(ValueError):
    """Raised for a malformed claim pattern."""


@dataclass(frozen=True)
class Conflict:
    task_a: str
    path_a: str
    task_b: str
    path_b: str
    sequential: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "task_a": self.task_a,
            "path_a": self.path_a,
            "task_b": self.task_b,
            "path_b": self.path_b,
            "sequential": self.sequential,
            "kind": "sequential-handoff" if self.sequential else "parallel-conflict",
        }


def normalize(path: str) -> str:
    """Normalise a claim path, rejecting anything that could escape the root."""
    if not isinstance(path, str):
        raise ClaimError(f"claim must be a string: {path!r}")
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        raise ClaimError("claim must not be empty")
    if _ABSOLUTE.match(cleaned):
        raise ClaimError(f"claim must be relative: {path!r}")
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ClaimError(f"claim must not contain '..': {path!r}")
        parts.append(part)
    if not parts:
        raise ClaimError(f"claim has no path segments: {path!r}")
    return "/".join(parts)


def _to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                if pattern[index : index + 3] == "**/":
                    out.append("(?:.*/)?")
                    index += 3
                    continue
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                raise ClaimError(f"unterminated character class in {pattern!r}")
            body = pattern[index + 1 : close]
            if body.startswith("!"):
                body = "^" + body[1:]
            try:
                re.compile("[" + body + "]")
            except re.error as exc:
                raise ClaimError(f"bad character class in {pattern!r}: {exc}") from exc
            out.append("[" + body + "]")
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    out.append("$")
    try:
        return re.compile("".join(out))
    except re.error as exc:  # pragma: no cover - guarded above
        raise ClaimError(f"bad claim pattern {pattern!r}: {exc}") from exc


def static_root(pattern: str) -> str:
    """Longest wildcard-free leading directory of a claim ("" = anywhere)."""
    parts = normalize(pattern).split("/")
    kept: list[str] = []
    for part in parts:
        if WILDCARD.search(part):
            break
        kept.append(part)
    if not kept:
        return ""
    # The kept segments are the directory the pattern lives in: "src/**" and
    # "src/*.rs" both root at "src", "src/rust/**" roots at "src/rust".
    return "/".join(kept)


def _root_related(root: str, path: str) -> bool:
    """True when *path* is *root* itself or lives underneath it."""
    if root == "":
        return True
    return path == root or path.startswith(root + "/")


def _roots_may_intersect(a: str, b: str) -> bool:
    if a == "" or b == "":
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def overlaps(a: str, b: str) -> bool:
    """True when two claims could resolve to the same file."""
    left = normalize(a)
    right = normalize(b)
    if left == right:
        return True

    left_literal = not WILDCARD.search(left)
    right_literal = not WILDCARD.search(right)

    if left_literal and right_literal:
        return (
            left.startswith(right + "/")
            or right.startswith(left + "/")
        )

    if left_literal:
        # The literal is either matched by the pattern, or it is a directory
        # the pattern's matches live under.
        return bool(_to_regex(right).match(left)) or _root_related(left, static_root(right))
    if right_literal:
        return bool(_to_regex(left).match(right)) or _root_related(right, static_root(left))

    if not _roots_may_intersect(static_root(left), static_root(right)):
        return False
    return True


def covers(claims: Sequence[str], path: str) -> bool:
    """True when *path* falls under at least one claim."""
    target = normalize(path)
    for claim in claims:
        pattern = normalize(claim)
        if _to_regex(pattern).match(target):
            return True
        if target.startswith(pattern + "/"):
            return True
    return False


def find_conflicts(
    owners: Sequence[tuple[str, Sequence[str]]],
    related: Callable[[str, str], bool] | None = None,
) -> list[Conflict]:
    """Pairwise claim conflicts.

    *owners* is ``[(task_id, claims), ...]``. *related* says whether two tasks
    have a dependency path; those pairs are reported with ``sequential=True``
    because a handoff may legitimately edit the same file, in order.
    """
    found: list[Conflict] = []
    for index, (task_a, claims_a) in enumerate(owners):
        for task_b, claims_b in owners[index + 1 :]:
            if task_a == task_b:
                continue
            for path_a in claims_a:
                for path_b in claims_b:
                    if overlaps(path_a, path_b):
                        ordered = bool(related(task_a, task_b)) if related else False
                        found.append(
                            Conflict(
                                task_a=task_a,
                                path_a=normalize(path_a),
                                task_b=task_b,
                                path_b=normalize(path_b),
                                sequential=ordered,
                            )
                        )
    return found


def check_dispatch(
    owners: Sequence[tuple[str, Sequence[str]]],
    running: Sequence[tuple[str, Sequence[str]]],
) -> list[Conflict]:
    """Conflicts between a candidate batch and work already in flight."""
    found: list[Conflict] = []
    for candidate_id, candidate_claims in owners:
        for running_id, running_claims in running:
            if candidate_id == running_id:
                continue
            for path_a in candidate_claims:
                for path_b in running_claims:
                    if overlaps(path_a, path_b):
                        found.append(
                            Conflict(
                                task_a=candidate_id,
                                path_a=normalize(path_a),
                                task_b=running_id,
                                path_b=normalize(path_b),
                            )
                        )
    return found