"""CMake builds for native code, host or Android NDK.

Two paths exist and they are not the same result. With an NDK the configure step
uses ``android.toolchain.cmake`` and produces Android ``.so`` files that can be
packaged. Without one, the host build still runs — useful for tests — but its
output is not Android code and the native stage is reported ``unsupported`` for
packaging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..util import proc
from . import env, stages
from .stages import Stage

VALID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")


def configure(
    source_dir: str | Path,
    build_dir: str | Path,
    *,
    abi: str = "arm64-v8a",
    api: int = 24,
    ndk: dict[str, Any] | None = None,
    release: bool = True,
    extra_args: Iterable[str] = (),
    timeout: int = 600,
) -> Stage:
    cmake = env.probe("cmake")
    if not cmake.ok:
        return stages.unsupported("configure", f"cmake is {cmake.state}: {cmake.error}")
    source = Path(source_dir).expanduser()
    build = Path(build_dir).expanduser()
    if not (source / "CMakeLists.txt").is_file():
        return stages.fail("configure", f"no CMakeLists.txt in {source}")
    build.mkdir(parents=True, exist_ok=True)

    argv = [
        str(cmake.path),
        "-S",
        str(source),
        "-B",
        str(build),
        "-DCMAKE_BUILD_TYPE=" + ("Release" if release else "Debug"),
    ]
    ndk = ndk if ndk is not None else env.find_ndk()
    if ndk.get("toolchain_file"):
        if abi not in VALID_ABIS:
            return stages.fail("configure", f"unsupported ABI {abi!r}")
        argv += [
            f"-DCMAKE_TOOLCHAIN_FILE={ndk['toolchain_file']}",
            f"-DANDROID_ABI={abi}",
            f"-DANDROID_PLATFORM=android-{int(api)}",
        ]
    argv.extend(extra_args)
    result = proc.run(argv, timeout=timeout)
    return stages.from_result(
        "configure",
        result,
        detail=f"configured for {abi} via NDK" if ndk.get("toolchain_file") else "configured for the host (no NDK)",
    )


def build(build_dir: str | Path, *, timeout: int = 3600, jobs: int | None = None) -> Stage:
    cmake = env.probe("cmake")
    if not cmake.ok:
        return stages.unsupported("compile", f"cmake is {cmake.state}: {cmake.error}")
    directory = Path(build_dir).expanduser()
    if not (directory / "CMakeCache.txt").is_file():
        return stages.fail("compile", f"{directory} is not a configured build tree")
    argv = [str(cmake.path), "--build", str(directory), "--parallel"]
    if jobs:
        argv.append(str(jobs))
    result = proc.run(argv, timeout=timeout)
    return stages.from_result("compile", result, detail="cmake build")


def test(build_dir: str | Path, *, timeout: int = 900) -> Stage:
    ctest = env.probe("ctest")
    if not ctest.ok:
        return stages.unsupported("ctest", "ctest is not available")
    directory = Path(build_dir).expanduser()
    result = proc.run(
        [str(ctest.path), "--test-dir", str(directory), "--output-on-failure"],
        timeout=timeout,
    )
    return stages.from_result("ctest", result, detail="ctest")


def collect_shared(build_dir: str | Path) -> list[Path]:
    directory = Path(build_dir).expanduser()
    if not directory.is_dir():
        return []
    return sorted(
        item
        for item in directory.rglob("*.so")
        if item.is_file() and "CMakeFiles" not in item.parts
    )


def android_libraries(source_dir: str | Path, build_dir: str | Path, *, abi: str, api: int = 24) -> dict[str, Any]:
    """Configure + build for Android, returning stages and the libraries found."""
    ndk = env.find_ndk()
    if not ndk.get("toolchain_file"):
        return {
            "stages": [
                stages.unsupported(
                    "configure",
                    "no NDK found; install the Android NDK or set ANDROID_NDK_ROOT to build Android .so files",
                ).as_dict()
            ],
            "libraries": {},
        }
    configure_stage = configure(source_dir, build_dir, abi=abi, api=api, ndk=ndk)
    result_stages = [configure_stage]
    libraries: list[str] = []
    if configure_stage.ok:
        compile_stage = build(build_dir)
        result_stages.append(compile_stage)
        if compile_stage.ok:
            libraries = [str(item) for item in collect_shared(build_dir)]
    return {
        "stages": [stage.as_dict() for stage in result_stages],
        "libraries": {abi: libraries} if libraries else {},
    }