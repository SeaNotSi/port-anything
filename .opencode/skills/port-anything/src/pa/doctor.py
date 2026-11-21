"""Environment detection for Termux, Android, desktop and build tooling.

Every field is measured, not assumed. Missing tools are reported as missing.
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from .util import proc

TOOL_PROBES = (
    ("rustc", ("--version",)),
    ("cargo", ("--version",)),
    ("clang", ("--version",)),
    ("clang++", ("--version",)),
    ("gcc", ("--version",)),
    ("cmake", ("--version",)),
    ("ninja", ("--version",)),
    ("python3", ("--version",)),
    ("java", ("-version",)),
    ("gradle", ("--version",)),
    ("git", ("--version",)),
    ("aapt2", ("version",)),
    ("d8", ("--version",)),
    ("apksigner", ("--version",)),
    ("zipalign", ("-h",)),
    ("adb", ("version",)),
    ("sqlite3", ("--version",)),
    ("pkg", ("--version",)),
)


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key.strip()] = int(parts[0]) * 1024  # kB -> bytes
    except OSError:
        pass
    return values


def load_average() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or Path("/data/data/com.termux/files/usr").is_dir()


def android_prop(name: str) -> str | None:
    result = proc.run(["getprop", name], timeout=5)
    if result.ok:
        value = result.stdout.strip()
        return value or None
    return None


def storage(path: str | Path) -> dict[str, int]:
    try:
        usage = shutil.disk_usage(str(path))
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        return {}


def tool_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name, flags in TOOL_PROBES:
        if shutil.which(name) is None:
            found[name] = None
            continue
        result = proc.run([name, *flags], timeout=20)
        text = (result.stdout or result.stderr).strip().splitlines()
        first = text[0].strip() if text else "present"
        if "CANNOT LINK EXECUTABLE" in first or "error while loading shared libraries" in first:
            found[name] = f"BROKEN: {first}"
        elif result.returncode != 0 and not text:
            found[name] = "present (probe failed)"
        else:
            found[name] = first
    return found


def sdk_paths() -> dict[str, str | None]:
    candidates = {
        "ANDROID_HOME": os.environ.get("ANDROID_HOME"),
        "ANDROID_SDK_ROOT": os.environ.get("ANDROID_SDK_ROOT"),
        "ANDROID_NDK_HOME": os.environ.get("ANDROID_NDK_HOME"),
        "ANDROID_NDK_ROOT": os.environ.get("ANDROID_NDK_ROOT"),
        "NDK_HOME": os.environ.get("NDK_HOME"),
    }
    for key, value in candidates.items():
        if value and not Path(value).expanduser().is_dir():
            candidates[key] = f"{value} (missing)"
    common = [
        Path.home() / "Android" / "Sdk",
        Path.home() / "android-sdk",
        Path("/usr/lib/android-sdk"),
        Path(os.environ.get("PREFIX", "/usr")) / "opt" / "android-sdk",
    ]
    detected = [str(path) for path in common if path.is_dir()]
    candidates["common_sdk_dirs"] = ", ".join(detected) if detected else None
    return candidates


def rust_android_std() -> dict[str, Any]:
    rustc = shutil.which("rustc")
    if rustc is None:
        return {"present": False, "reason": "rustc not on PATH"}
    result = proc.run([rustc, "--print", "target-list"], timeout=30)
    target_listed = "aarch64-linux-android" in result.stdout.split()
    sysroot = proc.run([rustc, "--print", "sysroot"], timeout=20).stdout.strip()
    std_path = Path(sysroot) / "lib" / "rustlib" / "aarch64-linux-android" / "lib" if sysroot else None
    return {
        "present": bool(std_path and std_path.is_dir()),
        "target_listed": target_listed,
        "sysroot": sysroot or None,
        "std_path": str(std_path) if std_path else None,
    }


def build_capabilities(report: dict[str, Any]) -> dict[str, str]:
    tools = report["tools"]
    sdk = report["sdk"]
    ndk_present = any(
        value and "missing" not in value
        for key, value in sdk.items()
        if key in {"ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "NDK_HOME"}
    )
    sdk_present = any(
        value and "missing" not in value
        for key, value in sdk.items()
        if key in {"ANDROID_HOME", "ANDROID_SDK_ROOT", "common_sdk_dirs"}
    )
    caps: dict[str, str] = {
        "analysis": "supported",
        "dataset": "supported" if tools.get("python3") else "unsupported",
        "rust_host_build": "supported" if tools.get("cargo") else "unsupported",
        "rust_android_target": "supported" if report["rust_android_std"]["present"] else "partial",
        "cpp_host_build": "supported" if tools.get("clang") or tools.get("gcc") else "unsupported",
        "cpp_android_build": "supported" if (ndk_present and tools.get("cmake")) else "unsupported",
        "apk_gradle": "supported" if (sdk_present and ndk_present and tools.get("gradle")) else "unsupported",
        "apk_fallback": "supported"
        if all(tools.get(name) for name in ("aapt2", "d8", "apksigner"))
        else "unsupported",
        "device_test": "supported"
        if (tools.get("adb") and not str(tools.get("adb")).startswith("BROKEN"))
        else "unsupported",
    }
    return caps


def detect() -> dict[str, Any]:
    mem = meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", mem.get("MemFree", 0))
    report: dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "termux": is_termux(),
            "prefix": os.environ.get("PREFIX"),
            "home": str(Path.home()),
            "tmpdir": os.environ.get("TMPDIR"),
            "android_api": android_prop("ro.build.version.sdk") if is_termux() else None,
            "android_abi": android_prop("ro.product.cpu.abi") if is_termux() else None,
            "kernel": _read_first_line("/proc/version"),
        },
        "cpu": {
            "count": os.cpu_count(),
            "load_average": load_average(),
        },
        "memory": {
            "total": total,
            "available": available,
            "used": max(total - available, 0) if total else 0,
            "swap_total": mem.get("SwapTotal", 0),
            "swap_free": mem.get("SwapFree", 0),
        },
        "storage": {
            "home": storage(Path.home()),
            "tmp": storage(os.environ.get("TMPDIR", "/tmp")),
        },
        "tools": tool_versions(),
        "sdk": sdk_paths(),
        "rust_android_std": rust_android_std(),
    }
    report["capabilities"] = build_capabilities(report)
    return report
