"""Safe process execution.

There is no ``shell=True`` path in this module by design. Callers pass an
argument vector; the vector is executed directly. Output is captured and
bounded so a hostile or broken tool cannot exhaust memory.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_TIMEOUT = 120
MAX_CAPTURE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def summary(self, limit: int = 400) -> str:
        text = (self.stdout or self.stderr).strip().replace("\n", " ")
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        return text


def _decode(raw: bytes) -> tuple[str, bool]:
    truncated = len(raw) > MAX_CAPTURE_BYTES
    if truncated:
        raw = raw[:MAX_CAPTURE_BYTES]
    return raw.decode("utf-8", errors="replace"), truncated


def run(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> Result:
    """Execute *argv* without a shell. Never raises on a non-zero exit."""
    if not argv:
        raise ValueError("argv must not be empty")
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of arguments, not a string")

    merged_env = dict(os.environ)
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            input=stdin,
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return Result(tuple(argv), 127, "", f"not found: {argv[0]}")
    except PermissionError:
        return Result(tuple(argv), 126, "", f"permission denied: {argv[0]}")
    except subprocess.TimeoutExpired as exc:
        out, t1 = _decode(exc.stdout or b"")
        err, t2 = _decode(exc.stderr or b"")
        return Result(tuple(argv), 124, out, err or f"timed out after {timeout}s", True, t1 or t2)

    out, t1 = _decode(completed.stdout or b"")
    err, t2 = _decode(completed.stderr or b"")
    return Result(tuple(argv), completed.returncode, out, err, False, t1 or t2)


def which(name: str) -> str | None:
    from shutil import which as _which

    return _which(name)


def version_of(name: str, *flags: str) -> str | None:
    """Best-effort version string for a tool, or None when absent."""
    if which(name) is None:
        return None
    result = run([name, *(flags or ("--version",))], timeout=20)
    if not result.ok and not result.stdout and not result.stderr:
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0].strip() if text else None
