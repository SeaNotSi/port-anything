"""Agent file registry and validation.

The nine agent files are part of the install, not documentation: OpenCode loads
``.opencode/agents/*.md`` and gives the orchestrator a subagent permission for
each specialist. If a file is missing or its frontmatter disagrees with its
filename, the orchestrator's permission list points at nothing and dispatch
fails at runtime. This module catches that before install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

EXPECTED_AGENTS: tuple[tuple[str, str], ...] = (
    ("port-orchestrator", "primary"),
    ("researcher", "subagent"),
    ("reverse-engineer", "subagent"),
    ("rust-engineer", "subagent"),
    ("cpp-engineer", "subagent"),
    ("android-engineer", "subagent"),
    ("graphics-engineer", "subagent"),
    ("optimizer", "subagent"),
    ("qa-engineer", "subagent"),
)

SPECIALIST_ROLES = tuple(name for name, mode in EXPECTED_AGENTS if mode == "subagent")


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    values: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values if closed else {}


def check(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).expanduser()
    missing: list[str] = []
    problems: list[str] = []
    found: list[str] = []

    for name, expected_mode in EXPECTED_AGENTS:
        path = root / f"{name}.md"
        if not path.is_file():
            missing.append(f"{name}.md")
            continue
        found.append(name)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{name}.md: unreadable ({exc})")
            continue
        meta = _frontmatter(text)
        if not meta:
            problems.append(f"{name}.md: no frontmatter block")
            continue
        declared = meta.get("name", "")
        if declared and declared != name:
            problems.append(f"{name}.md: frontmatter name {declared!r} does not match filename")
        mode = meta.get("mode", "")
        if not mode:
            problems.append(f"{name}.md: frontmatter has no mode")
        elif mode != expected_mode:
            problems.append(f"{name}.md: mode {mode!r}, expected {expected_mode!r}")
        if len(meta.get("description", "")) < 20:
            problems.append(f"{name}.md: description is too short to route on")
        body = text.split("---", 2)[-1].strip()
        if len(body) < 200:
            problems.append(f"{name}.md: body is too thin to be an instruction set")

    extra = sorted(
        path.stem
        for path in root.glob("*.md")
        if path.stem not in {name for name, _ in EXPECTED_AGENTS}
    )
    return {
        "ok": not missing and not problems,
        "directory": str(root),
        "expected": len(EXPECTED_AGENTS),
        "found": found,
        "missing": missing,
        "problems": problems,
        "extra": extra,
    }


def render_check(result: dict[str, Any]) -> str:
    lines = [
        f"agents  : {len(result['found'])}/{result['expected']} present in {result['directory']}",
    ]
    for name in result["missing"]:
        lines.append(f"missing : {name}")
    for problem in result["problems"]:
        lines.append(f"problem : {problem}")
    for name in result["extra"]:
        lines.append(f"extra   : {name}.md (not part of the nine)")
    lines.append("OK" if result["ok"] else "FAILED")
    return "\n".join(lines)