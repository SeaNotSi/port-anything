"""Android toolchain discovery.

Everything here is measured. A tool that is on ``PATH`` but cannot execute is
``broken``, not ``available``: this host ships an ``adb`` binary that fails to
link, and reporting it as present would produce a build report claiming device
tests are possible when they are not.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util import proc

VERSION_ARGS: dict[str, tuple[str, ...]] = {
    "aapt2": ("version",),
    "d8": ("--version",),
    "apksigner": ("version",),
    "zipalign": ("-h",),
    "gradle": ("--version",),
    "javac": ("-version",),
    "java": ("-version",),
    "keytool": ("-help",),
    "adb": ("version",),
    "cmake": ("--version",),
    "ninja": ("--version",),
    "clang": ("--version",),
    "cargo": ("--version",),
    "rustc": ("--version",),
    "python3": ("--version",),
}

ANDROID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
_BROKEN_HINTS = ("CANNOT LINK EXECUTABLE", "error while loading shared libraries", "Exec format error")


@dataclass(frozen=True)
class Tool:
    name: str
    path: str | None
    version: str = ""
    state: str = "missing"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "state": self.state,
            "error": self.error,
        }


def _first_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:160]
    return ""


def probe(name: str, timeout: int = 20) -> Tool:
    path = proc.which(name)
    if path is None:
        return Tool(name=name, path=None, state="missing", error="not on PATH")
    result = proc.run([path, *VERSION_ARGS.get(name, ("--version",))], timeout=timeout)
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode in (126, 127) or any(hint in combined for hint in _BROKEN_HINTS):
        return Tool(
            name=name,
            path=path,
            state="broken",
            error=_first_line(combined) or f"exit {result.returncode}",
        )
    version = _first_line(combined)
    # A tool that prints no version but exits 0 is usable; older zipalign does
    # exactly that with -h.
    state = "ok" if (result.returncode == 0 or version) and version else "broken"
    return Tool(name=name, path=path, version=version, state=state, error="" if state == "ok" else "no usable output")


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_SDK"):
        value = os.environ.get(key)
        if value:
            dirs.append(Path(value).expanduser())
    prefix = os.environ.get("PREFIX")
    home = Path.home()
    dirs.extend(
        [
            home / "Android" / "Sdk",
            home / "android-sdk",
            home / "sdk",
        ]
    )
    if prefix:
        dirs.append(Path(prefix) / "opt" / "android-sdk")
    dirs.append(Path("/opt/android-sdk"))
    return dirs


def find_sdk() -> dict[str, Any]:
    for directory in _candidate_dirs():
        if not directory.is_dir():
            continue
        platforms = sorted((directory / "platforms").glob("android-*")) if (directory / "platforms").is_dir() else []
        framework = None
        api = None
        for platform in reversed(platforms):
            jar = platform / "android.jar"
            if jar.is_file():
                framework = str(jar)
                match = re.search(r"android-(\d+)", platform.name)
                api = int(match.group(1)) if match else None
                break
        return {"root": str(directory), "framework_jar": framework, "api": api, "platforms": [p.name for p in platforms]}
    return {"root": None, "framework_jar": None, "api": None, "platforms": []}


def find_ndk() -> dict[str, Any]:
    candidates: list[Path] = []
    for key in ("ANDROID_NDK_ROOT", "ANDROID_NDK_HOME", "NDK_ROOT", "NDK_HOME"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value).expanduser())
    sdk = find_sdk()["root"]
    if sdk:
        candidates.extend(sorted((Path(sdk) / "ndk").glob("*")))
    prefix = os.environ.get("PREFIX")
    if prefix:
        candidates.extend([Path(prefix) / "opt" / "android-ndk", Path(prefix) / "opt" / "ndk"])
    for directory in candidates:
        toolchain = directory / "build" / "cmake" / "android.toolchain.cmake"
        if toolchain.is_file():
            return {"root": str(directory), "toolchain_file": str(toolchain)}
    return {"root": None, "toolchain_file": None}


def detect() -> dict[str, Any]:
    tools = {name: probe(name) for name in VERSION_ARGS}
    sdk = find_sdk()
    ndk = find_ndk()
    adb = tools["adb"]
    capabilities = {
        "apk_fallback": "supported"
        if all(tools[name].ok for name in ("aapt2", "d8", "apksigner"))
        else "unsupported",
        "apk_framework_resources": "supported" if sdk["framework_jar"] else "unsupported",
        "apk_zipalign": "supported" if tools["zipalign"].ok else "unsupported",
        "apk_signing": "supported" if tools["apksigner"].ok else "unsupported",
        "apk_gradle": "supported"
        if (sdk["root"] and ndk["root"] and tools["gradle"].ok)
        else "unsupported",
        "cpp_android_build": "supported" if (ndk["root"] and tools["cmake"].ok) else "unsupported",
        "rust_android_target": "supported" if tools["cargo"].ok else "unsupported",
        "device_test": "supported" if adb.ok else "unsupported",
    }
    return {
        "tools": {name: tool.as_dict() for name, tool in tools.items()},
        "android": {
            "sdk_root": sdk["root"],
            "framework_jar": sdk["framework_jar"],
            "api": sdk["api"],
            "ndk_root": ndk["root"],
            "ndk_toolchain_file": ndk["toolchain_file"],
            "abis": list(ANDROID_ABIS),
            "adb": adb.as_dict(),
            "adb_reason": adb.error if not adb.ok else "",
        },
        "capabilities": capabilities,
    }


def require(name: str, report: dict[str, Any] | None = None) -> Tool:
    """Return the probed tool, or raise with the reason it cannot be used."""
    tool = Tool(**{**probe(name).as_dict()})
    if not tool.ok:
        raise RuntimeError(f"{name} is {tool.state}: {tool.error or 'no usable output'}")
    _ = report
    return tool