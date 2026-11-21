"""Port strategy selection and staged progress tracking.

Seven distinct routes are modelled, and they are not interchangeable:

``already_android``
    The artifact is already an Android package. That is not a port. The work is
    adaptation and verification.
``native_port``
    Source is available and there is no engine in the way: rebuild for Android.
``engine_recompile``
    Source plus a supported engine export path: re-export, do not rewrite.
``adaptation``
    Binary only, engine runtime is replaceable: patch the platform glue.
``asset_migration``
    Binary only, but assets are extractable: rebuild the game around them.
``compatibility_layer``
    Foreign-architecture binary: translate the CPU and OS surface.
``emulator``
    Disc or console image: run it under an emulator core.

Every stage carries its own status. Nothing is upgraded without evidence, and
``port_verified`` stays false until a launch and gameplay check is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STRATEGY_IDS = (
    "already_android",
    "native_port",
    "engine_recompile",
    "adaptation",
    "asset_migration",
    "compatibility_layer",
    "emulator",
)

STRATEGY_NAMES = {
    "already_android": "already Android (adapt and verify)",
    "native_port": "native port from source",
    "engine_recompile": "engine recompile / re-export",
    "adaptation": "binary adaptation",
    "asset_migration": "asset migration to a new runtime",
    "compatibility_layer": "compatibility layer (CPU + OS translation)",
    "emulator": "emulator",
}

STAGE_NAMES = {
    "identify": "identify",
    "source": "source",
    "assets": "assets",
    "toolchain": "toolchain",
    "build": "build",
    "package": "package",
    "install": "install",
    "launch": "launch",
    "verified": "verified",
}

RECOMPILE_ENGINES = {
    "godot",
    "love2d",
    "cocos2dx",
    "unreal",
    "bevy",
    "monogame",
    "libgdx",
    "ogre",
    "idtech3",
    "idtech4",
    "source1",
    "rpgmaker",
    "defold",
}

HEAVY_ENGINES = {"unreal", "source2", "idtech4"}

EMULATOR_ONLY_ENGINES = {"goldsrc", "source1", "source2", "idtech3"}

_RECORD_CACHE: dict[str, dict[str, Any] | None] = {}
_DATASET = {"checked": False, "available": False, "error": None}


@dataclass
class Strategy:
    id: str
    name: str
    score: int
    confidence: str
    rationale: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "score": self.score,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "requirements": self.requirements,
            "blockers": self.blockers,
            "evidence": self.evidence,
            "stages": self.stages,
        }


def _record(record_id: str) -> dict[str, Any] | None:
    if record_id in _RECORD_CACHE:
        return _RECORD_CACHE[record_id]
    value: dict[str, Any] | None = None
    try:
        from ..dataset import query

        value = query.get(record_id)
        _DATASET["available"] = True
    except Exception as exc:  # index missing or unreadable: degrade, do not crash
        _DATASET["available"] = False
        _DATASET["error"] = f"{type(exc).__name__}: {exc}"
    _RECORD_CACHE[record_id] = value
    return value


def _dataset_available() -> bool:
    _record("engine.unknown")  # primes the availability flag without a real record
    _DATASET["checked"] = True
    return _DATASET["available"]


def _engine_facts(engine: str | None) -> dict[str, Any]:
    if not engine:
        return {}
    record = _record(f"engine.{engine}")
    if not record:
        return {}
    compatibility = record.get("compatibility", {})
    return {
        "record": record["id"],
        "name": record.get("name"),
        "android": compatibility.get("android"),
        "arm64": compatibility.get("arm64"),
        "verification": record.get("verification"),
        "last_verified": record.get("last_verified"),
        "requirements": list(record.get("requirements", [])),
        "limitations": list(record.get("limitations", [])),
    }


def _stage(stage_id: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": stage_id, "name": STAGE_NAMES[stage_id], "status": status, "detail": detail}


def _toolchain_stage(capabilities: dict[str, str] | None) -> dict[str, Any]:
    if capabilities is None:
        return _stage("toolchain", "unknown", "build capabilities were not probed")
    gradle = capabilities.get("apk_gradle") == "supported"
    native = capabilities.get("cpp_android_build") == "supported"
    fallback = capabilities.get("apk_fallback") == "supported"
    if gradle or native or fallback:
        routes = [
            label
            for label, ok in (("gradle", gradle), ("ndk/cmake", native), ("aapt2+d8+apksigner", fallback))
            if ok
        ]
        return _stage("toolchain", "ready", "available: " + ", ".join(routes))
    missing = [
        label
        for label, ok in (("gradle/sdk/ndk", gradle), ("ndk/cmake", native), ("aapt2+d8+apksigner", fallback))
        if not ok
    ]
    return _stage("toolchain", "blocked", "no Android build route: " + ", ".join(missing))


def _package_stage(capabilities: dict[str, str] | None) -> dict[str, Any]:
    if capabilities is None:
        return _stage("package", "unknown", "packaging capability was not probed")
    if capabilities.get("apk_gradle") == "supported":
        return _stage("package", "ready", "Gradle can assemble a signed APK")
    if capabilities.get("apk_fallback") == "supported":
        return _stage("package", "ready", "aapt2+d8+apksigner fallback can assemble an APK")
    return _stage("package", "blocked", "no APK assembler available")


def _install_stage(capabilities: dict[str, str] | None) -> dict[str, Any]:
    if capabilities is None:
        return _stage("install", "unknown", "device capability was not probed")
    if capabilities.get("device_test") == "supported":
        return _stage("install", "ready", "adb can reach a device")
    return _stage("install", "blocked", "adb is unavailable or broken on this host")


def _base_stages(
    strategy_id: str,
    fp: dict[str, Any],
    capabilities: dict[str, str] | None,
    needs_source: bool,
) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = [
        _stage("identify", "done", f"format={fp.get('format')} engine={fp.get('top_engine') or 'unknown'}")
    ]
    if needs_source:
        if fp.get("source_available"):
            stages.append(_stage("source", "done", "project source detected"))
        else:
            stages.append(_stage("source", "blocked", "no project source was found"))
    else:
        stages.append(_stage("source", "not_applicable", "this route does not rebuild from source"))

    if fp.get("assets"):
        stages.append(_stage("assets", "done", "asset containers: " + ", ".join(fp["assets"])))
    else:
        stages.append(_stage("assets", "unknown", "no asset container identified"))

    stages.append(_toolchain_stage(capabilities))
    stages.append(_stage("build", "unknown", "not attempted during analysis"))
    stages.append(_package_stage(capabilities))
    stages.append(_install_stage(capabilities))
    stages.append(_stage("launch", "unknown", "no launch evidence recorded"))
    stages.append(_stage("verified", "unknown", "requires launch plus gameplay evidence"))
    return stages


def _strategy(
    strategy_id: str,
    score: int,
    rationale: list[str],
    requirements: list[str],
    evidence: list[str],
    blockers: list[str],
    fp: dict[str, Any],
    capabilities: dict[str, str] | None,
    needs_source: bool = False,
) -> Strategy:
    confidence = "high" if score >= 14 else "medium" if score >= 8 else "low"
    return Strategy(
        id=strategy_id,
        name=STRATEGY_NAMES[strategy_id],
        score=score,
        confidence=confidence,
        rationale=rationale,
        requirements=requirements,
        blockers=blockers,
        evidence=evidence,
        stages=_base_stages(strategy_id, fp, capabilities, needs_source),
    )


def _candidates(
    fp: dict[str, Any],
    target_platform: str,
    capabilities: dict[str, str] | None,
) -> list[Strategy]:
    engine = fp.get("top_engine")
    facts = _engine_facts(engine)
    android_support = facts.get("android")
    source = bool(fp.get("source_available"))
    assets = list(fp.get("assets", []))
    archs = set(fp.get("architectures", []))
    build_systems = list(fp.get("build_systems", []))
    languages = set(fp.get("languages", []))
    candidates: list[Strategy] = []

    if fp.get("android_runtime"):
        score = 20 if fp.get("format") in ("apk", "aab") else 14
        candidates.append(
            _strategy(
                "already_android",
                score,
                [
                    "the artifact already runs an Android runtime",
                    "an existing APK is not evidence of a port",
                    "the remaining work is adaptation, re-signing and verification",
                ],
                [
                    "confirm the package targets the device ABI and Android API level",
                    "re-sign with a key you control before distribution",
                    "record launch and gameplay evidence before claiming success",
                ],
                [f"format={fp.get('format')} platforms={','.join(fp.get('platforms', []))}"],
                [],
                fp,
                capabilities,
            )
        )

    if source and not engine:
        score = 12 + (3 if build_systems else 0) + (2 if languages & {"c", "cpp", "rust"} else 0)
        blockers = [] if build_systems else ["no recognized build system; a build description must be written"]
        candidates.append(
            _strategy(
                "native_port",
                score,
                ["source is available and no engine owns the platform layer"],
                [
                    "an arm64-v8a build via CMake or the existing build system",
                    "an Android platform layer for windowing, input, audio and storage",
                    "asset paths moved into the APK asset or OBB layout",
                ],
                [f"build systems: {', '.join(build_systems) or 'none'}"],
                blockers,
                fp,
                capabilities,
                needs_source=True,
            )
        )

    if source and engine in RECOMPILE_ENGINES:
        score = 14 + (3 if android_support == "supported" else 0) - (2 if engine in HEAVY_ENGINES else 0)
        blockers: list[str] = []
        if android_support == "unsupported":
            blockers.append(f"{facts.get('record', engine)} has no supported Android target in PAKB")
        requirements = [
            f"matching {engine} toolchain and Android export module",
            "Android SDK and NDK on the build host",
            "re-export or re-cook content for arm64-v8a",
        ] + facts.get("requirements", [])[:3]
        candidates.append(
            _strategy(
                "engine_recompile",
                score,
                ["source is present and the engine has an Android export path"],
                requirements,
                [f"engine={engine} android={android_support or 'unknown'}"],
                blockers,
                fp,
                capabilities,
                needs_source=True,
            )
        )

    if engine and not source and (fp.get("android_runtime") or fp.get("format") in ("apk", "aab")):
        score = 10 + (2 if android_support == "supported" else 0)
        blockers = []
        if android_support == "unsupported":
            blockers.append(f"{facts.get('record', engine)} has no supported Android target in PAKB")
        candidates.append(
            _strategy(
                "adaptation",
                score,
                [
                    "the engine runtime is shipped for Android but the source is not",
                    "the work is patching runtime and platform glue, not rebuilding the game",
                ],
                [
                    "instrument or decompile the shipped runtime",
                    "replace platform glue with Android equivalents",
                    "repackage the result and re-sign it",
                ],
                [f"engine={engine} android={android_support or 'unknown'}"],
                blockers,
                fp,
                capabilities,
            )
        )

    if assets:
        score = 8 + (2 if engine else 0)
        blockers = [] if engine else ["a destination runtime must be chosen before assets can be mapped"]
        candidates.append(
            _strategy(
                "asset_migration",
                score,
                ["asset containers are readable; the runtime is not"],
                [
                    "extract every container and index its contents",
                    "map asset formats into the destination engine",
                    "recreate scenes, physics and scripted logic from evidence",
                ],
                [f"assets: {', '.join(assets)}"],
                blockers,
                fp,
                capabilities,
            )
        )

    if not source and archs & {"x86", "x86_64"} and target_platform == "android":
        score = 9 + (2 if fp.get("format") == "pe" else 0)
        candidates.append(
            _strategy(
                "compatibility_layer",
                score,
                ["the binary targets a foreign CPU and OS surface"],
                [
                    "box64 or FEX-Emu for x86-64 to arm64 translation",
                    "Wine for the Win32 API surface",
                    "DXVK or wined3d for Direct3D",
                    "per-title tuning of the translation stack",
                ],
                [f"architectures: {', '.join(sorted(archs))}"],
                [],
                fp,
                capabilities,
            )
        )

    if fp.get("disc_image") or fp.get("family") == "disc":
        score = 14 + (3 if fp.get("format") == "iso9660" else 0)
        candidates.append(
            _strategy(
                "emulator",
                score,
                ["the artifact is a disc image, not an installable program"],
                [
                    "an emulator core for the source platform",
                    "firmware the core requires, supplied by the user",
                    "touch controls mapped to the original input",
                    "a frame-time budget measured on the target device",
                ],
                [f"format={fp.get('format')}"],
                [],
                fp,
                capabilities,
            )
        )

    candidates.sort(key=lambda item: (-item.score, STRATEGY_IDS.index(item.id)))
    return candidates


def assess(
    fp: dict[str, Any],
    target_platform: str = "android",
    target_abi: str = "arm64-v8a",
    capabilities: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rank port strategies for a fingerprint and report stage-by-stage status."""
    dataset_ok = _dataset_available()
    facts = _engine_facts(fp.get("top_engine"))
    strategies = _candidates(fp, target_platform, capabilities)
    recommended = strategies[0] if strategies else None

    blockers: list[str] = []
    next_actions: list[str] = []
    if recommended:
        blockers.extend(recommended.blockers)
        for stage in recommended.stages:
            if stage["status"] == "blocked":
                blockers.append(f"{stage['name']}: {stage['detail']}")
        for stage in recommended.stages:
            if stage["status"] in ("blocked", "unknown"):
                verb = "resolve" if stage["status"] == "blocked" else "produce evidence"
                next_actions.append(f"{verb} [{stage['name']}]: {stage['detail']}")
        done = [stage["id"] for stage in recommended.stages if stage["status"] == "done"]
        blocked = [stage["id"] for stage in recommended.stages if stage["status"] == "blocked"]
    else:
        done = []
        blocked = []
        next_actions.append("no port route matched; record the format and engine evidence first")

    claims = {
        "apk_present": fp.get("format") in ("apk", "aab"),
        "port_complete": False,
        "port_verified": False,
        "stages_done": done,
        "stages_blocked": blocked,
        "statement": "only stages listed as done have evidence; no port is claimed",
    }

    return {
        "target": {"platform": target_platform, "abi": target_abi},
        "dataset": {
            "available": dataset_ok,
            "error": _DATASET.get("error"),
            "engine_facts": facts,
        },
        "strategies": [strategy.as_dict() for strategy in strategies],
        "recommended": recommended.id if recommended else None,
        "recommended_confidence": recommended.confidence if recommended else None,
        "blockers": blockers,
        "next_actions": next_actions[:8],
        "claims": claims,
    }
