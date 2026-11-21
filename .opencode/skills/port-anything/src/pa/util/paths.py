"""Path resolution and containment checks.

Every filesystem write in Port Anything goes through :func:`resolve_inside`.
Symlinks are resolved before the containment check so a link cannot be used to
escape an allowed root.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/pa/util/paths.py -> util -> pa -> src -> <skill root>
SKILL_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = SKILL_ROOT / "src"
CONFIG_DIR = SKILL_ROOT / "config"
DATASETS_DIR = SKILL_ROOT / "datasets"
RECORDS_DIR = DATASETS_DIR / "records"
SCHEMA_DIR = DATASETS_DIR / "schema"
INDEX_DIR = DATASETS_DIR / "index"
SOURCES_FILE = DATASETS_DIR / "sources" / "source-registry.json"
MANIFEST_FILE = DATASETS_DIR / "manifest.json"
TEMPLATES_DIR = SKILL_ROOT / "templates"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
# Agents live beside the skills directory inside the same .opencode (or
# ~/.config/opencode for a global install), so one tree move keeps them paired.
AGENTS_DIR = SKILL_ROOT.parents[1] / "agents"


class PathEscapeError(ValueError):
    """Raised when a path resolves outside the root it is supposed to live in."""


def _cache_root() -> Path:
    override = os.environ.get("PA_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "port-anything"
    return Path.home() / ".cache" / "port-anything"


CACHE_ROOT = _cache_root()


def state_dir() -> Path:
    override = os.environ.get("PA_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return CACHE_ROOT / "state"


def resolve_inside(path: str | Path, root: str | Path) -> Path:
    """Resolve *path* and guarantee it stays inside *root*.

    Returns the resolved path. Raises :class:`PathEscapeError` otherwise.
    """
    root_resolved = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathEscapeError(f"{resolved} escapes allowed root {root_resolved}") from exc
    return resolved


def ensure_dir(path: str | Path, root: str | Path | None = None) -> Path:
    """Create a directory, optionally enforcing containment."""
    target = resolve_inside(path, root) if root is not None else Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def skill_root() -> Path:
    return SKILL_ROOT


def project_root(start: str | Path | None = None) -> Path:
    """Walk upward looking for an opencode.jsonc or .opencode directory."""
    current = Path(start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "opencode.jsonc").is_file() or (candidate / ".opencode").is_dir():
            return candidate
    return current


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{count} B"
