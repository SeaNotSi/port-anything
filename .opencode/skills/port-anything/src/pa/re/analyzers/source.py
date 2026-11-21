"""Source project analysis: layout, markers, engine, dependencies, targets."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "target", "build", "dist", "out",
    "Library", "Temp", "obj", "bin", "Binaries", "Intermediate", "DerivedDataCache",
    ".gradle", ".idea", ".vs", "__pycache__", ".venv", "venv", "vendor",
}

MAX_FILES = 50_000
MAX_BYTES = 64 * 1024 * 1024

MARKERS = {
    "CMakeLists.txt": ("buildsystem", "cmake"),
    "Makefile": ("buildsystem", "make"),
    "meson.build": ("buildsystem", "meson"),
    "build.gradle": ("buildsystem", "gradle"),
    "build.gradle.kts": ("buildsystem", "gradle"),
    "settings.gradle": ("buildsystem", "gradle"),
    "Cargo.toml": ("language", "rust"),
    "package.json": ("language", "javascript"),
    "*.uproject": ("engine", "unreal"),
    "project.godot": ("engine", "godot"),
    "*.sln": ("buildsystem", "msbuild"),
    "*.csproj": ("language", "csharp"),
    "*.yyp": ("engine", "gamemaker"),
    "conf.lua": ("engine", "love2d"),
    "main.lua": ("engine", "love2d"),
    "*.rpy": ("engine", "renpy"),
    "pubspec.yaml": ("language", "dart"),
    "AndroidManifest.xml": ("platform", "android"),
}

ENGINE_BY_MARKER = {
    "*.uproject": "unreal",
    "project.godot": "godot",
    "*.yyp": "gamemaker",
    "conf.lua": "love2d",
    "*.rpy": "renpy",
}


def _walk(root: Path) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for path in root.rglob("*"):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        if path.is_dir():
            continue
        try:
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
                continue
        except ValueError:
            continue
        files.append(path)
    return files, truncated


def _read_text(path: Path, limit: int = 256 * 1024) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _cargo_dependencies(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    deps: list[str] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            deps.extend(block.keys())
    workspace = data.get("workspace", {})
    if isinstance(workspace, dict) and isinstance(workspace.get("dependencies"), dict):
        deps.extend(workspace["dependencies"].keys())
    return sorted(set(deps))


def _package_json_dependencies(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            deps.extend(block.keys())
    return sorted(set(deps))


def _csproj_dependencies(text: str) -> list[str]:
    return sorted(set(re.findall(r'PackageReference\s+Include="([^"]+)"', text)))


def _cmake_packages(text: str) -> list[str]:
    return sorted(set(re.findall(r"find_package\s*\(\s*([A-Za-z0-9_]+)", text)))


class SourceProjectAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="source_project",
        name="Source project analyzer",
        formats=("directory",),
        inputs=("directory",),
        output_schema="source_project.v1",
        dependencies=(),
        limitations=(
            "Reads at most 50,000 files and 256 KiB per manifest.",
            "Dependency extraction covers Cargo, npm, csproj and CMake find_package.",
            "It identifies a project, not a complete build graph.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "directory"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        root = context.path
        files, truncated = _walk(root)
        extensions = Counter()
        total_bytes = 0
        for path in files:
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue
            suffix = path.suffix.lower() or "(none)"
            extensions[suffix] += 1
            if total_bytes > MAX_BYTES:
                truncated = True
                break

        found: dict[str, list[str]] = {}
        for path in files:
            name = path.name
            for pattern, (kind, label) in MARKERS.items():
                if pattern.startswith("*"):
                    matched = name.endswith(pattern[1:])
                else:
                    matched = name == pattern
                if matched:
                    found.setdefault(f"{kind}:{label}", []).append(str(path.relative_to(root)))

        engines = sorted(
            {
                engine
                for pattern, engine in ENGINE_BY_MARKER.items()
                if any(str(path.relative_to(root)).endswith(pattern[1:]) if pattern.startswith("*") else path.name == pattern for path in files)
            }
        )

        dependencies: dict[str, list[str]] = {}
        engine_metadata: dict[str, Any] = {}
        for path in files:
            name = path.name
            if name == "Cargo.toml":
                dependencies["cargo"] = _cargo_dependencies(_read_text(path))
            elif name == "package.json":
                dependencies["npm"] = _package_json_dependencies(_read_text(path))
            elif name.endswith(".csproj"):
                dependencies.setdefault("nuget", []).extend(_csproj_dependencies(_read_text(path)))
            elif name == "CMakeLists.txt":
                dependencies.setdefault("cmake", []).extend(_cmake_packages(_read_text(path)))
            elif name.endswith(".uproject"):
                try:
                    data = json.loads(_read_text(path))
                    engine_metadata["uproject"] = {
                        "file": str(path.relative_to(root)),
                        "engine_association": data.get("EngineAssociation"),
                        "modules": [m.get("Name") for m in data.get("Modules", []) if isinstance(m, dict)],
                    }
                    engines.append("unreal")
                except json.JSONDecodeError:
                    pass
            elif name == "project.godot":
                text = _read_text(path)
                version = re.search(r'config/features=PackedStringArray\("([^"]+)"', text)
                engine_metadata["godot"] = {
                    "file": str(path.relative_to(root)),
                    "feature": version.group(1) if version else None,
                }
                engines.append("godot")
            elif name.endswith(".yyp"):
                engines.append("gamemaker")
            elif name.endswith(".rpy"):
                engines.append("renpy")

        for key in list(dependencies):
            dependencies[key] = sorted(set(dependencies[key]))

        android_targets = sorted(
            {
                str(path.relative_to(root))
                for path in files
                if any(part in {"Android", "android", "AndroidPlayer"} for part in path.relative_to(root).parts)
            }
        )[:20]

        return {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "truncated": truncated,
            "extensions": dict(extensions.most_common(25)),
            "markers": {key: value[:20] for key, value in sorted(found.items())},
            "engines": sorted(set(engines)),
            "engine_metadata": engine_metadata,
            "dependencies": dependencies,
            "android_target_files": android_targets,
            "source_available": True,
        }


class BuildSystemAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="build_system",
        name="Build system detector",
        formats=("directory", "cmake", "make", "meson"),
        inputs=("directory", "file"),
        output_schema="build_system.v1",
        dependencies=(),
        limitations=(
            "Marker-file detection only; it does not parse the full build graph.",
            "Nested projects are reported, not resolved into one build.",
        ),
        verification="verified",
    )

    FILES = {
        "CMakeLists.txt": "cmake",
        "CMakeCache.txt": "cmake",
        "Makefile": "make",
        "GNUmakefile": "make",
        "meson.build": "meson",
        "build.gradle": "gradle",
        "build.gradle.kts": "gradle",
        "settings.gradle": "gradle",
        "settings.gradle.kts": "gradle",
        "Cargo.toml": "cargo",
        "Android.mk": "ndk-build",
        "Application.mk": "ndk-build",
        "premake5.lua": "premake",
        "*.sln": "msbuild",
        "*.vcxproj": "msbuild",
    }

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id in ("directory", "unknown") or context.path.name in self.FILES

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        detected: list[dict[str, Any]] = []
        if path.is_dir():
            for name, system in self.FILES.items():
                if name.startswith("*"):
                    matches = list(path.rglob(name))[:10]
                    if matches:
                        detected.append({"system": system, "files": [str(m.relative_to(path)) for m in matches]})
                else:
                    candidate = path / name
                    if candidate.is_file():
                        detected.append({"system": system, "files": [name]})
        else:
            for name, system in self.FILES.items():
                if name.startswith("*"):
                    if path.name.endswith(name[1:]):
                        detected.append({"system": system, "files": [path.name]})
                elif path.name == name:
                    detected.append({"system": system, "files": [path.name]})
        return {"detected": detected, "systems": sorted({entry["system"] for entry in detected})}


register(SourceProjectAnalyzer())
register(BuildSystemAnalyzer())
