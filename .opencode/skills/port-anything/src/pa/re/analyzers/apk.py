"""APK analyzer: manifest, native libraries, dex, assets and engine hints."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from ..axml import AxmlError, parse_axml
from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

ENGINE_MARKERS = {
    "lib/arm64-v8a/libunity.so": "unity",
    "lib/arm64-v8a/libil2cpp.so": "unity",
    "lib/armeabi-v7a/libunity.so": "unity",
    "lib/armeabi-v7a/libil2cpp.so": "unity",
    "lib/arm64-v8a/libUE4.so": "unreal",
    "lib/arm64-v8a/libUnreal.so": "unreal",
    "lib/arm64-v8a/libgodot_android.so": "godot",
    "lib/arm64-v8a/libyoyo.so": "gamemaker",
    "lib/arm64-v8a/liblove.so": "love2d",
    "lib/arm64-v8a/libcocos2djs.so": "cocos2dx",
}


def _manifest_summary(axml: dict[str, Any]) -> dict[str, Any]:
    root = next((element for element in axml["elements"] if element["name"] == "manifest"), None)
    if root is None:
        return {"error": "no manifest element found"}
    attributes = root["attributes"]
    summary: dict[str, Any] = {
        "package": attributes.get("package"),
        "version_code": attributes.get("versionCode"),
        "version_name": attributes.get("versionName"),
        "compile_sdk": attributes.get("compileSdkVersion"),
    }
    permissions: list[str] = []
    activities: list[str] = []
    application: dict[str, Any] = {}
    for element in axml["elements"]:
        if element["name"] == "uses-sdk":
            summary["min_sdk"] = element["attributes"].get("minSdkVersion")
            summary["target_sdk"] = element["attributes"].get("targetSdkVersion")
        elif element["name"] == "uses-permission":
            name = element["attributes"].get("name")
            if name:
                permissions.append(str(name))
        elif element["name"] == "application":
            application = {
                "name": element["attributes"].get("name"),
                "debuggable": element["attributes"].get("debuggable"),
                "extract_native_libs": element["attributes"].get("extractNativeLibs"),
                "uses_cleartext_traffic": element["attributes"].get("usesCleartextTraffic"),
            }
        elif element["name"] in ("activity", "activity-alias"):
            name = element["attributes"].get("name")
            if name:
                activities.append(str(name))
    summary["permissions"] = sorted(set(permissions))
    summary["activities"] = activities[:64]
    summary["application"] = application
    summary["elements"] = axml["element_count"]
    return summary


class APKAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="apk",
        name="APK parser",
        formats=("apk", "aab"),
        inputs=("file",),
        output_schema="apk.v1",
        dependencies=("fileformat.apk", "fileformat.dex"),
        limitations=(
            "Signatures are listed but not cryptographically verified here; use apksigner for that.",
            "Split APKs are inspected individually, not reassembled.",
            "Binary XML parsing covers the manifest elements this tool needs, not every resource.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id in ("apk", "aab")

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            manifest_name = "AndroidManifest.xml"
            if manifest_name not in names:
                manifest_name = "base/manifest/AndroidManifest.xml"
            manifest: dict[str, Any] = {}
            if manifest_name in names:
                try:
                    manifest = _manifest_summary(parse_axml(archive.read(manifest_name)))
                except (AxmlError, KeyError, ValueError) as exc:
                    manifest = {"error": f"manifest parse failed: {exc}"}

            native: dict[str, list[dict[str, Any]]] = {}
            dex: list[dict[str, Any]] = []
            assets = {"count": 0, "bytes": 0}
            signatures: list[str] = []
            for info in infos:
                name = info.filename
                if name.startswith("lib/") and name.endswith(".so"):
                    parts = name.split("/")
                    if len(parts) >= 3:
                        native.setdefault(parts[1], []).append(
                            {"name": parts[-1], "size": info.file_size}
                        )
                elif name.endswith(".dex"):
                    dex.append({"name": name, "size": info.file_size})
                elif name.startswith("assets/"):
                    assets["count"] += 1
                    assets["bytes"] += info.file_size
                elif name.startswith("META-INF/") and name.endswith((".RSA", ".DSA", ".EC", ".SF")):
                    signatures.append(name)

            engine_hints = sorted({engine for name, engine in ENGINE_MARKERS.items() if name in names})
            if any(name.startswith("assets/bin/Data/") for name in names):
                engine_hints.append("unity")
            if any(name.startswith("assets/") and name.endswith(".pck") for name in names):
                engine_hints.append("godot")
            if any(name.startswith("assets/") and name.endswith(".rpa") for name in names):
                engine_hints.append("renpy")

            return {
                "size": path.stat().st_size,
                "entry_count": len(infos),
                "uncompressed_bytes": sum(info.file_size for info in infos),
                "manifest": manifest,
                "native_libraries": native,
                "abis": sorted(native),
                "dex": dex,
                "assets": assets,
                "signature_files": signatures,
                "engine_hints": sorted(set(engine_hints)),
                "has_v1_signature": bool(signatures),
                "is_aab": context.sniffed.format_id == "aab",
            }


register(APKAnalyzer())
