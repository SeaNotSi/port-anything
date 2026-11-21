"""Engine, runtime and platform fingerprinting.

Evidence is collected from every analyzer and weighted by strength:

* ``certain``  — a container header or project file that only one engine writes
* ``likely``   — a shipped runtime library, an APK native library, a version string
* ``possible`` — a textual marker that can survive in data unrelated to the engine

Textual markers alone never produce a certain fingerprint, and a packed binary
that yields no markers is reported as unknown rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .sniff import FormatGuess

WEIGHT = {"certain": 10, "likely": 6, "possible": 2}
MAX_SCORE = 20

ENGINE_NAMES = {
    "unity": "Unity",
    "unreal": "Unreal Engine",
    "godot": "Godot",
    "gamemaker": "GameMaker",
    "cocos2dx": "Cocos2d-x",
    "defold": "Defold",
    "source2": "Source 2",
    "source1": "Source",
    "goldsrc": "GoldSrc",
    "idtech3": "id Tech 3",
    "idtech4": "id Tech 4",
    "renpy": "Ren'Py",
    "love2d": "LÖVE",
    "libgdx": "libGDX",
    "bevy": "Bevy",
    "monogame": "MonoGame",
    "rpgmaker": "RPG Maker",
    "ogre": "OGRE",
    "web_electron": "Electron",
}

FORMAT_ENGINE = {
    "unity_assetbundle": "unity",
    "unity_global_metadata": "unity",
    "ue_uasset": "unreal",
    "ue_pak": "unreal",
    "godot_pck": "godot",
    "gamemaker_datawin": "gamemaker",
    "renpy_rpa": "renpy",
    "love_archive": "love2d",
}

ASSET_ANALYZERS = {
    "unity_bundle",
    "unity_metadata",
    "unreal_package",
    "unreal_pak",
    "godot_pck",
    "gamemaker_data",
    "renpy_archive",
    "love_archive",
}

# (substring, engine) checked against lowercased library names.
LIBRARY_ENGINES = (
    ("unityplayer", "unity"),
    ("libunity", "unity"),
    ("libue4", "unreal"),
    ("libue5", "unreal"),
    ("libunreal", "unreal"),
    ("ue4game", "unreal"),
    ("ue5game", "unreal"),
    ("libgodot", "godot"),
    ("libyoyo", "gamemaker"),
    ("libcocos", "cocos2dx"),
    ("liblove", "love2d"),
)

LIBRARY_RUNTIMES = (
    ("libil2cpp", "il2cpp"),
    ("il2cpp", "il2cpp"),
    ("libmonosgen", "mono"),
    ("mono-2.0", "mono"),
    ("mscoree", "dotnet"),
    ("coreclr", "dotnet"),
    ("hostfxr", "dotnet"),
    ("libpython", "python"),
    ("liblua", "lua"),
    ("luajit", "luajit"),
)

GRAPHICS_LIBRARIES = (
    ("vulkan-1", "vulkan"),
    ("libvulkan", "vulkan"),
    ("d3d9", "d3d9"),
    ("d3d11", "d3d11"),
    ("d3d12", "d3d12"),
    ("opengl32", "opengl"),
    ("libglesv2", "opengl_es"),
    ("libegl", "opengl_es"),
    ("libgl.so", "opengl"),
    ("metal", "metal"),
    ("webgpu", "webgpu"),
)

ARCHITECTURES = {
    "x86-64": "x86_64",
    "x86_64": "x86_64",
    "i386": "x86",
    "x86": "x86",
    "aarch64": "arm64",
    "arm64": "arm64",
    "arm": "arm32",
    "armv7 (thumb-2)": "arm32",
    "armeabi-v7a": "arm32",
    "arm64-v8a": "arm64",
    "risc-v": "riscv64",
    "risc-v 64": "riscv64",
    "powerpc": "ppc",
    "powerpc64": "ppc64",
}

LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".cs": "csharp",
    ".gd": "gdscript",
    ".lua": "lua",
    ".py": "python",
    ".rpy": "renpy",
    ".java": "java",
    ".kt": "kotlin",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
}


@dataclass
class _Evidence:
    source: str
    confidence: str
    detail: str

    def key(self) -> tuple[str, str]:
        return (self.source, self.detail)


@dataclass
class _EngineBucket:
    engine: str
    evidence: list[_Evidence] = field(default_factory=list)

    def add(self, source: str, confidence: str, detail: str) -> None:
        candidate = _Evidence(source, confidence, detail)
        if candidate.key() not in {item.key() for item in self.evidence}:
            self.evidence.append(candidate)

    @property
    def score(self) -> int:
        return min(MAX_SCORE, sum(WEIGHT[item.confidence] for item in self.evidence))

    @property
    def confidence(self) -> str:
        score = self.score
        if score >= 10:
            return "certain"
        if score >= 6:
            return "likely"
        return "possible"


def _result(results: Iterable[dict[str, Any]], analyzer_id: str) -> dict[str, Any] | None:
    for entry in results:
        if entry.get("analyzer") == analyzer_id and isinstance(entry.get("result"), dict):
            return entry["result"]
    return None


def _normalise_arch(value: str | None) -> str | None:
    if not value:
        return None
    return ARCHITECTURES.get(value.strip().lower())


def _library_names(analyzer_id: str, result: dict[str, Any]) -> list[str]:
    if analyzer_id == "pe":
        return [str(name) for name in result.get("imported_dlls", [])]
    if analyzer_id == "elf":
        names = [str(name) for name in result.get("needed", [])]
        if result.get("soname"):
            names.append(str(result["soname"]))
        return names
    if analyzer_id == "macho":
        return [str(name) for name in result.get("dylibs", [])]
    return []


def _collect_platform(
    sniffed: FormatGuess,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    platforms: set[str] = set()
    architectures: set[str] = set()
    android_runtime = False
    disc_image = sniffed.format_id == "iso9660"

    pe = _result(results, "pe")
    if pe:
        platforms.add("windows")
        arch = _normalise_arch(pe.get("machine"))
        if arch:
            architectures.add(arch)

    elf = _result(results, "elf")
    if elf:
        arch = _normalise_arch(elf.get("machine"))
        if arch:
            architectures.add(arch)
        if elf.get("is_android"):
            platforms.add("android")
            android_runtime = True
        else:
            platforms.add("linux")

    macho = _result(results, "macho")
    if macho:
        slices = macho.get("slices") if macho.get("fat") else [macho]
        for slice_data in slices or []:
            arch = _normalise_arch(slice_data.get("cputype"))
            if arch:
                architectures.add(arch)
            build = slice_data.get("build") or {}
            platform = build.get("platform")
            if platform in ("macos", "ios", "tvos", "watchos", "maccatalyst", "iossimulator"):
                platforms.add("ios" if platform.startswith("ios") else platform)

    apk = _result(results, "apk")
    if apk:
        platforms.add("android")
        android_runtime = True
        for abi in apk.get("abis", []):
            arch = _normalise_arch(abi)
            if arch:
                architectures.add(arch)

    source = _result(results, "source_project")
    if source:
        markers = source.get("markers", {})
        if any(key.startswith("platform:android") for key in markers):
            platforms.add("android")

    return {
        "platforms": sorted(platforms),
        "architectures": sorted(architectures),
        "android_runtime": android_runtime,
        "disc_image": disc_image,
    }


def fingerprint(sniffed: FormatGuess, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate analyzer results into ranked engine candidates and platform facts."""
    buckets: dict[str, _EngineBucket] = {}
    notes: list[str] = []

    def add(engine: str, source: str, confidence: str, detail: str) -> None:
        buckets.setdefault(engine, _EngineBucket(engine)).add(source, confidence, detail)

    format_engine = FORMAT_ENGINE.get(sniffed.format_id)
    if format_engine:
        add(format_engine, "container", "certain", f"{sniffed.format_id} container")

    apk = _result(results, "apk")
    if apk:
        for engine in apk.get("engine_hints", []):
            add(engine, "apk_native", "likely", f"APK ships the {engine} runtime")

    markers = _result(results, "marker_scan")
    if markers:
        for engine in markers.get("grouped", {}).get("engine", []):
            add(engine, "marker", "possible", f"textual marker for {engine}")
        for engine, version in markers.get("versions", {}).items():
            add(engine, "version", "likely", f"{engine} version string {version}")

    source = _result(results, "source_project")
    if source:
        for engine in source.get("engines", []):
            add(engine, "project", "certain", f"project file for {engine}")

    for analyzer_id in ("pe", "elf", "macho"):
        binary = _result(results, analyzer_id)
        if not binary:
            continue
        for name in _library_names(analyzer_id, binary):
            lowered = name.lower()
            for needle, engine in LIBRARY_ENGINES:
                if needle in lowered:
                    add(engine, "binary_import", "likely", f"{analyzer_id} imports {name}")

    runtime: set[str] = set()
    middleware: set[str] = set()
    graphics: set[str] = set()

    if markers:
        grouped = markers.get("grouped", {})
        runtime.update(grouped.get("runtime", []))
        middleware.update(grouped.get("middleware", []))
        graphics.update(grouped.get("graphics", []))
        if markers.get("truncated"):
            notes.append("marker scan stopped at its byte ceiling; later markers were not seen")

    for analyzer_id in ("pe", "elf", "macho"):
        binary = _result(results, analyzer_id)
        if not binary:
            continue
        for name in _library_names(analyzer_id, binary):
            lowered = name.lower()
            for needle, label in LIBRARY_RUNTIMES:
                if needle in lowered:
                    runtime.add(label)
            for needle, label in GRAPHICS_LIBRARIES:
                if needle in lowered:
                    graphics.add(label)

    if apk:
        for name in apk.get("native_libraries", {}).get("arm64-v8a", []):
            lowered = str(name.get("name", "")).lower()
            if "il2cpp" in lowered:
                runtime.add("il2cpp")
            if "mono" in lowered:
                runtime.add("mono")

    platform = _collect_platform(sniffed, results)

    assets: list[str] = []
    for analyzer_id in ASSET_ANALYZERS:
        if _result(results, analyzer_id) is not None:
            assets.append(analyzer_id)
    if apk and apk.get("assets", {}).get("count"):
        assets.append("apk_assets")

    build_systems: list[str] = []
    build = _result(results, "build_system")
    if build:
        build_systems = list(build.get("systems", []))

    languages: set[str] = set()
    if source:
        for extension in source.get("extensions", {}):
            language = LANGUAGE_BY_EXTENSION.get(extension.lower())
            if language:
                languages.add(language)

    engines = sorted(
        (
            {
                "id": bucket.engine,
                "dataset_id": f"engine.{bucket.engine}",
                "name": ENGINE_NAMES.get(bucket.engine, bucket.engine),
                "score": bucket.score,
                "confidence": bucket.confidence,
                "evidence": [
                    {"source": item.source, "confidence": item.confidence, "detail": item.detail}
                    for item in bucket.evidence
                ],
            }
            for bucket in buckets.values()
        ),
        key=lambda entry: (-entry["score"], entry["id"]),
    )

    if not engines:
        notes.append("no engine evidence found; the binary may be packed, stripped or custom")
    if sniffed.confidence != "certain":
        notes.append(f"format identification is {sniffed.confidence}, not certain")

    return {
        "format": sniffed.format_id,
        "family": sniffed.family,
        "engines": engines,
        "top_engine": engines[0]["id"] if engines else None,
        "runtime": sorted(runtime),
        "middleware": sorted(middleware),
        "graphics": sorted(graphics),
        "platforms": platform["platforms"],
        "architectures": platform["architectures"],
        "android_runtime": platform["android_runtime"],
        "disc_image": platform["disc_image"],
        "source_available": bool(source and source.get("source_available")),
        "languages": sorted(languages),
        "assets": sorted(assets),
        "build_systems": build_systems,
        "notes": notes,
    }
