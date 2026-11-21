"""Installer for the Port Anything skill.

Two scopes:

``project``  ``<project>/.opencode/{skills/port-anything,agents}`` — discovered
             by walking up from the current directory for ``opencode.jsonc`` or
             ``.opencode``.
``global``   ``$XDG_CONFIG_HOME/opencode`` (default ``~/.config/opencode``),
             which is where OpenCode looks for global skills and agents.

The installer is idempotent and non-destructive:

* a file is written only when its content hash differs from the manifest;
* files that vanished from the source tree are removed on the next install;
* an existing ``opencode.jsonc`` is never modified, so the model selection and
  permissions the user already chose stay exactly as they are;
* uninstall touches only files recorded in the manifest whose content still
  matches, and leaves anything edited by hand in place (reported, not deleted).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import paths
from .util.hash import sha256_file

MANIFEST_NAME = ".port-anything-manifest.json"
SCHEMA_VERSION = 1

# Directories that are build output or caches, never installed.
IGNORE_DIRS = {"__pycache__", ".git", "target", "build", ".pytest_cache", ".mypy_cache"}
IGNORE_SUFFIXES = {".pyc", ".pyo", ".idsig"}
IGNORE_FILES = {MANIFEST_NAME}


def discover_agents_dir(skill_root: Path | None = None) -> Path:
    root = Path(skill_root) if skill_root else paths.SKILL_ROOT
    return root.parents[1] / "agents"


def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        parts = set(item.relative_to(root).parts)
        if parts & IGNORE_DIRS:
            continue
        if item.suffix in IGNORE_SUFFIXES:
            continue
        if item.name in IGNORE_FILES:
            continue
        found.append(item)
    return found


def collect_sources(skill_root: Path | None = None, agents_dir: Path | None = None) -> dict[str, Path]:
    """Map install-relative paths to source files.

    Relative paths are written under the target's ``.opencode`` root, so a
    project install produces ``.opencode/skills/port-anything/...`` and
    ``.opencode/agents/*.md``.
    """
    root = Path(skill_root) if skill_root else paths.SKILL_ROOT
    agents = Path(agents_dir) if agents_dir else discover_agents_dir(root)
    sources: dict[str, Path] = {}
    for item in _iter_files(root):
        sources[("skills/port-anything/" + item.relative_to(root).as_posix())] = item
    for item in _iter_files(agents):
        if item.suffix == ".md":
            sources[("agents/" + item.name)] = item
    return sources


def target_root(scope: str, project: str | Path | None = None) -> Path:
    if scope == "project":
        return paths.project_root(project) / ".opencode"
    if scope == "global":
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base).expanduser() if base else Path.home() / ".config"
        return root / "opencode"
    raise ValueError(f"unknown scope {scope!r}; expected 'project' or 'global'")


@dataclass
class Action:
    relative: str
    state: str  # create | update | unchanged | remove
    source: str = ""
    target: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative": self.relative,
            "state": self.state,
            "source": self.source,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass
class Report:
    scope: str
    target: str
    actions: list[Action] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    dry_run: bool = False

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.state] = counts.get(action.state, 0) + 1
        return counts

    @property
    def changed(self) -> list[Action]:
        return [action for action in self.actions if action.state in ("create", "update", "remove")]

    def ok(self) -> bool:
        return not any(message.startswith("error:") for message in self.messages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "target": self.target,
            "dry_run": self.dry_run,
            "counts": self.counts(),
            "actions": [action.as_dict() for action in self.actions],
            "messages": list(self.messages),
            "ok": self.ok(),
        }

    def render(self) -> str:
        lines = [f"scope    : {self.scope}", f"target   : {self.target}"]
        if self.dry_run:
            lines.append("dry-run  : no files were written")
        counts = self.counts()
        summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "none"
        lines.append(f"actions  : {summary}")
        for action in self.actions:
            if action.state != "unchanged":
                lines.append(f"  {action.state:<9} {action.relative}")
        for message in self.messages:
            lines.append(message)
        lines.append("OK" if self.ok() else "FAILED")
        return "\n".join(lines)


def load_manifest(target: Path) -> dict[str, Any]:
    path = target / MANIFEST_NAME
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"schema_version": SCHEMA_VERSION, "files": {}}
    return data


def _save_manifest(target: Path, files: dict[str, str]) -> None:
    path = target / MANIFEST_NAME
    payload = {"schema_version": SCHEMA_VERSION, "files": dict(sorted(files.items()))}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_plan(target: Path, sources: dict[str, Path], manifest: dict[str, Any]) -> list[Action]:
    known: dict[str, str] = manifest.get("files", {})
    actions: list[Action] = []
    for relative, source in sorted(sources.items()):
        destination = target / relative
        source_hash = sha256_file(source)
        if not destination.is_file():
            state = "create"
            reason = "not installed"
        elif sha256_file(destination) != source_hash:
            # Compare against the manifest as well: a file the user edited is
            # still overwritten, but the reason records that it differs.
            state = "update"
            reason = "content differs from the source"
        else:
            state = "unchanged"
            reason = "already current"
        actions.append(Action(relative, state, str(source), str(destination), reason))
    for relative in sorted(set(known) - set(sources)):
        actions.append(
            Action(
                relative,
                "remove",
                "",
                str(target / relative),
                "present in the manifest but no longer in the source tree",
            )
        )
    return actions


def apply_plan(target: Path, plan: list[Action], *, dry_run: bool = False) -> dict[str, Any]:
    written: list[str] = []
    removed: list[str] = []
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    next_files: dict[str, str] = {}
    for action in plan:
        if action.state == "remove":
            if not dry_run:
                try:
                    Path(action.target).unlink()
                except OSError:
                    continue
            removed.append(action.relative)
            continue
        if action.state == "unchanged":
            next_files[action.relative] = sha256_file(action.source)
            continue
        destination = Path(action.target)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.source, destination)
        written.append(action.relative)
        next_files[action.relative] = sha256_file(action.source)
    if not dry_run:
        _save_manifest(target, next_files)
    return {"written": written, "removed": removed, "files": next_files}


def install(
    scope: str = "project",
    project: str | Path | None = None,
    *,
    dry_run: bool = False,
    skill_root: Path | None = None,
    agents_dir: Path | None = None,
) -> Report:
    target = target_root(scope, project)
    sources = collect_sources(skill_root, agents_dir)
    report = Report(scope=scope, target=str(target), dry_run=dry_run)
    if not sources:
        report.messages.append("error: no source files found to install")
        return report
    if scope == "project":
        config = target.parent / "opencode.jsonc"
        if config.is_file():
            report.messages.append("note: existing opencode.jsonc left untouched (model selection preserved)")
        else:
            source_root = Path(skill_root) if skill_root else paths.SKILL_ROOT
            template = source_root / "templates" / "opencode.project.jsonc"
            if template.is_file():
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(template, config)
                report.messages.append("create: opencode.jsonc from template (no model pinned)")
            else:
                report.messages.append("note: no opencode.jsonc present and no template found")
    manifest = load_manifest(target)
    plan = build_plan(target, sources, manifest)
    report.actions = plan
    result = apply_plan(target, plan, dry_run=dry_run)
    report.messages.append(
        f"result: {len(result['written'])} written, {len(result['removed'])} removed"
    )
    return report


def uninstall(
    scope: str = "project",
    project: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> Report:
    target = target_root(scope, project)
    report = Report(scope=scope, target=str(target), dry_run=dry_run)
    manifest = load_manifest(target)
    known: dict[str, str] = manifest.get("files", {})
    if not known:
        report.messages.append("note: no manifest found; nothing recorded to uninstall")
        return report
    for relative, recorded_hash in sorted(known.items()):
        path = target / relative
        if not path.is_file():
            continue
        if sha256_file(path) != recorded_hash:
            report.actions.append(
                Action(relative, "unchanged", "", str(path), "edited since install; left in place")
            )
            continue
        report.actions.append(Action(relative, "remove", "", str(path), "installed by this tool"))
    if not dry_run:
        for action in report.actions:
            if action.state == "remove":
                try:
                    Path(action.target).unlink()
                except OSError:
                    continue
        manifest_path = target / MANIFEST_NAME
        try:
            manifest_path.unlink()
        except OSError:
            pass
        _prune_empty(target)
    report.messages.append(f"result: {report.counts().get('remove', 0)} removed")
    return report


def _prune_empty(target: Path) -> None:
    """Remove now-empty directories the installer created, deepest first."""
    if not target.is_dir():
        return
    for directory in sorted((item for item in target.rglob("*") if item.is_dir()), reverse=True):
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            continue
    try:
        if not any(target.iterdir()):
            target.rmdir()
    except OSError:
        pass


def check(scope: str = "project", project: str | Path | None = None, *, skill_root: Path | None = None, agents_dir: Path | None = None) -> Report:
    target = target_root(scope, project)
    sources = collect_sources(skill_root, agents_dir)
    report = Report(scope=scope, target=str(target))
    if not target.is_dir():
        report.messages.append("error: target is not installed")
        return report
    manifest = load_manifest(target)
    known: dict[str, str] = manifest.get("files", {})
    missing = [relative for relative in sources if not (target / relative).is_file()]
    stale = [relative for relative in known if not (target / relative).is_file()]
    drifted = [
        relative
        for relative in sources
        if (target / relative).is_file() and sha256_file(target / relative) != sha256_file(sources[relative])
    ]
    for relative in sorted(missing):
        report.actions.append(Action(relative, "create", str(sources[relative]), "", "missing"))
    for relative in sorted(stale):
        report.actions.append(Action(relative, "update", "", str(target / relative), "recorded but absent"))
    for relative in sorted(drifted):
        report.actions.append(Action(relative, "update", str(sources[relative]), "", "differs from source"))
    if not report.actions:
        report.messages.append("note: installation is current")
    report.messages.append(
        f"checked: {len(sources)} source file(s), {len(report.actions)} difference(s)"
    )
    return report
