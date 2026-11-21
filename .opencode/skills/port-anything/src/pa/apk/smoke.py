"""Device smoke test through adb.

A package that was built and signed is still not a port. This module is the only
place that can move ``install``, ``launch`` and ``runtime`` beyond ``unknown``,
and it refuses to do so on a host where adb cannot execute. The local adb binary
on Termux fails to link, so those stages are ``unsupported`` with the loader
error attached — never ``passed``.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..util import proc
from . import env
from .stages import Stage, fail, ok, unsupported

_LOG_LINE = re.compile(r"^[VDIWEF]/\S+\(\s*\d+\): ")


def state() -> dict[str, str]:
    tool = env.probe("adb")
    if tool.ok:
        return {"state": "ok", "path": tool.path or "", "version": tool.version}
    return {"state": tool.state, "path": tool.path or "", "error": tool.error}


def devices(*, timeout: int = 20) -> Stage:
    tool = env.probe("adb")
    if not tool.ok:
        return unsupported("devices", f"adb is {tool.state}: {tool.error}")
    result = proc.run([str(tool.path), "devices"], timeout=timeout)
    if not result.ok:
        return fail("devices", "adb devices failed", command=list(result.argv), returncode=result.returncode, output=result.summary())
    lines = [line.split("\t")[0] for line in result.stdout.splitlines()[1:] if "\tdevice" in line]
    if not lines:
        return fail("devices", "no device attached", command=list(result.argv), returncode=result.returncode, output=result.summary())
    return ok("devices", f"device(s): {', '.join(lines)}", command=list(result.argv), output=result.stdout.strip())


def install(apk: str | Path, *, timeout: int = 300) -> Stage:
    tool = env.probe("adb")
    if not tool.ok:
        return unsupported("install", f"adb is {tool.state}: {tool.error}")
    path = Path(apk)
    if not path.is_file():
        return fail("install", f"APK missing: {path}")
    result = proc.run([str(tool.path), "install", "-r", str(path)], timeout=timeout)
    if result.ok and "Success" in result.stdout:
        return ok("install", "installed on device", command=list(result.argv), output=result.stdout.strip())
    return fail("install", "adb install did not report Success", command=list(result.argv), returncode=result.returncode, output=result.summary())


def launch(package: str, activity: str = "", *, timeout: int = 30) -> Stage:
    tool = env.probe("adb")
    if not tool.ok:
        return unsupported("launch", f"adb is {tool.state}: {tool.error}")
    if not package:
        return fail("launch", "no package name given")
    component = f"{package}/{activity}" if activity else ""
    argv = [str(tool.path), "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
    if component:
        argv = [str(tool.path), "shell", "am", "start", "-n", component]
    result = proc.run(argv, timeout=timeout)
    if result.ok:
        return ok("launch", f"launch intent accepted for {package}", command=list(argv), output=result.summary())
    return fail("launch", "launch intent failed", command=list(argv), returncode=result.returncode, output=result.summary())


def logcat(*, seconds: int = 5, package: str = "", timeout: int = 60) -> Stage:
    tool = env.probe("adb")
    if not tool.ok:
        return unsupported("runtime", f"adb is {tool.state}: {tool.error}")
    argv = [str(tool.path), "logcat", "-d", "-v", "brief", "-t", str(max(1, seconds) * 40)]
    result = proc.run(argv, timeout=timeout)
    if not result.ok:
        return fail("runtime", "logcat read failed", command=list(argv), returncode=result.returncode, output=result.summary())
    lines = [line for line in result.stdout.splitlines() if _LOG_LINE.match(line)]
    if package:
        lines = [line for line in lines if package in line]
    crashes = [line for line in lines if "FATAL EXCEPTION" in line or "ANR in" in line]
    if crashes:
        return fail("runtime", "crash recorded in logcat", command=list(argv), output="\n".join(crashes[:20]))
    if not lines:
        return fail("runtime", "no log lines for the package; launch may not have started", command=list(argv), output=result.summary())
    return ok("runtime", f"{len(lines)} log line(s) observed", command=list(argv), output="\n".join(lines[:20]))