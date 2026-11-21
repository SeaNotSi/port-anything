"""Bounded marker scan for engine, middleware and graphics API signatures."""

from __future__ import annotations

import re
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

SCAN_LIMIT = 32 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
OVERLAP = 64

MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"UnityPlayer", "engine:unity"),
    (b"il2cpp", "engine:unity"),
    (b"UnityFS", "engine:unity"),
    (b"UnrealEngine", "engine:unreal"),
    (b"++UE4+", "engine:unreal"),
    (b"++UE5+", "engine:unreal"),
    (b"Godot Engine", "engine:godot"),
    (b"GameMaker", "engine:gamemaker"),
    (b"Ren'Py", "engine:renpy"),
    (b"L\xd6VE", "engine:love2d"),
    (b"cocos2d", "engine:cocos2dx"),
    (b"vulkan-1.dll", "graphics:vulkan"),
    (b"libvulkan.so", "graphics:vulkan"),
    (b"VK_KHR_android_surface", "graphics:vulkan"),
    (b"d3d11.dll", "graphics:d3d11"),
    (b"d3d12.dll", "graphics:d3d12"),
    (b"d3d9.dll", "graphics:d3d9"),
    (b"opengl32.dll", "graphics:opengl"),
    (b"libGLESv2.so", "graphics:opengl_es"),
    (b"libEGL.so", "graphics:opengl_es"),
    (b"SDL2", "middleware:sdl2"),
    (b"SDL3", "middleware:sdl3"),
    (b"FMOD", "middleware:fmod"),
    (b"Wwise", "middleware:wwise"),
    (b"OpenAL", "middleware:openal"),
    (b"XAudio2", "middleware:xaudio2"),
    (b"PhysX", "middleware:physx"),
    (b"Havok", "middleware:havok"),
    (b"SteamAPI", "middleware:steam"),
    (b"Bink", "middleware:bink"),
    (b"Scaleform", "middleware:scaleform"),
    (b"mono-2.0", "runtime:mono"),
    (b"libmonosgen", "runtime:mono"),
    (b"mscoree.dll", "runtime:dotnet"),
    (b"libil2cpp.so", "runtime:il2cpp"),
    (b"liboboe", "audio:oboe"),
)

VERSION_PATTERNS = (
    ("unity", re.compile(rb"(\d{4}\.\d+\.\d+[abfp]\d+)")),
    ("unreal", re.compile(rb"\+\+UE(\d)\+Release-(\d+\.\d+)")),
    ("godot", re.compile(rb"Godot Engine v(\d+\.\d+(?:\.\d+)?)")),
)


class MarkerScanAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="marker_scan",
        name="Binary marker and version scan",
        formats=("pe", "elf", "macho", "apk", "unknown"),
        inputs=("file",),
        output_schema="marker_scan.v1",
        dependencies=(),
        limitations=(
            "Scans at most 32 MiB, so markers past that offset are missed.",
            "A marker means the string is present, not that the component is used.",
            "Packed or encrypted binaries yield no markers.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id in ("pe", "elf", "macho", "apk", "unknown")

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        size = path.stat().st_size
        counts: dict[str, int] = {}
        versions: dict[str, str] = {}
        scanned = 0
        carry = b""
        with path.open("rb") as handle:
            while scanned < min(size, SCAN_LIMIT):
                block = handle.read(CHUNK)
                if not block:
                    break
                window = carry + block
                for marker, label in MARKERS:
                    found = window.count(marker)
                    if found:
                        counts[label] = counts.get(label, 0) + found
                for engine, pattern in VERSION_PATTERNS:
                    if engine in versions:
                        continue
                    match = pattern.search(window)
                    if match:
                        versions[engine] = match.group(1).decode("ascii", errors="replace") if match.lastindex == 1 else ".".join(
                            part.decode("ascii", errors="replace") for part in match.groups()
                        )
                scanned += len(block)
                carry = window[-OVERLAP:]

        grouped: dict[str, list[str]] = {}
        for label in sorted(counts):
            category, _, name = label.partition(":")
            grouped.setdefault(category, []).append(name)

        return {
            "scanned_bytes": scanned,
            "truncated": size > scanned,
            "markers": counts,
            "grouped": grouped,
            "versions": versions,
        }


register(MarkerScanAnalyzer())
