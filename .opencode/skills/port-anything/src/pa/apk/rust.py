"""Rust build for Android targets.

Cargo can only build for a target whose ``std`` is installed. This module checks
that first with ``rustc --print target-libdir`` and reports the missing target as
a blocker instead of letting cargo fail with a wall of linker output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..util import proc
from . import env, stages
from .stages import Stage

ABI_TARGETS: dict[str, str] = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "armv7-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
}


def target_libdir(target: str, *, timeout: int = 30) -> dict[str, Any]:
    rustc = env.probe("rustc")
    if not rustc.ok:
        return {"present": False, "path": None, "error": f"rustc is {rustc.state}"}
    result = proc.run([str(rustc.path), "--print", "target-libdir", "--target", target], timeout=timeout)
    if not result.ok:
        return {"present": False, "path": None, "error": result.summary()}
    libdir = Path(result.stdout.strip())
    if not libdir.is_dir():
        return {"present": False, "path": str(libdir), "error": "target std directory does not exist"}
    return {"present": True, "path": str(libdir), "error": ""}


def build(
    project_dir: str | Path,
    *,
    abi: str = "arm64-v8a",
    release: bool = True,
    offline: bool = False,
    extra_args: Iterable[str] = (),
    timeout: int = 3600,
) -> dict[str, Any]:
    project = Path(project_dir).expanduser()
    if abi not in ABI_TARGETS:
        return {"stage": stages.fail("rust", f"unsupported ABI {abi!r}").as_dict(), "libraries": {}}
    target = ABI_TARGETS[abi]
    if not (project / "Cargo.toml").is_file():
        return {"stage": stages.fail("rust", f"no Cargo.toml in {project}").as_dict(), "libraries": {}}

    cargo = env.probe("cargo")
    if not cargo.ok:
        return {"stage": stages.unsupported("rust", f"cargo is {cargo.state}: {cargo.error}").as_dict(), "libraries": {}}

    std = target_libdir(target)
    if not std["present"]:
        return {
            "stage": stages.unsupported(
                "rust",
                f"target {target} is not installed: {std['error']} (run: rustup target add {target})",
            ).as_dict(),
            "libraries": {},
        }

    argv = [str(cargo.path), "build", "--target", target]
    if release:
        argv.append("--release")
    if offline:
        argv.append("--offline")
    argv.extend(extra_args)
    result = proc.run(argv, cwd=project, timeout=timeout)

    profile = "release" if release else "debug"
    out_dir = project / "target" / target / profile
    libraries = sorted(out_dir.glob("*.so")) if out_dir.is_dir() else []
    stage: Stage = stages.from_result(
        "rust",
        result,
        detail=f"cargo build for {target}",
        artifacts=[str(item) for item in libraries],
    )
    if stage.ok and not libraries:
        stage = stages.fail("rust", f"cargo succeeded but no .so was produced in {out_dir}")
    return {
        "stage": stage.as_dict(),
        "libraries": {abi: [str(item) for item in libraries]},
        "target": target,
    }


def collect(project_dir: str | Path, abi: str = "arm64-v8a", *, release: bool = True) -> list[Path]:
    project = Path(project_dir).expanduser()
    target = ABI_TARGETS.get(abi)
    if not target:
        return []
    directory = project / "target" / target / ("release" if release else "debug")
    return sorted(directory.glob("*.so")) if directory.is_dir() else []