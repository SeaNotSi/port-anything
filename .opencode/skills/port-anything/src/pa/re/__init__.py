"""Reverse-engineering module.

Public surface:

``analyze(path)``
    Sniff the format, run every analyzer that claims it, and aggregate the
    evidence into an engine/platform fingerprint.

``assess(...)``
    Turn a fingerprint into ranked port strategies with per-stage status.

Nothing here reports success it did not observe. An analyzer error is recorded
as an error and the remaining analyzers still run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import analyzers as _analyzers  # noqa: F401  (registers analyzers)
from .compat import STRATEGY_IDS, STRATEGY_NAMES, assess
from .fingerprint import fingerprint
from .registry import run, specs
from .report import render_markdown, render_text
from .sniff import FormatGuess, sniff

__all__ = [
    "STRATEGY_IDS",
    "STRATEGY_NAMES",
    "FormatGuess",
    "analyze",
    "assess",
    "fingerprint",
    "render_markdown",
    "render_text",
    "sniff",
    "specs",
]


def analyze(path: str | Path) -> dict[str, Any]:
    """Analyze *path* and return format, analyzer results and fingerprint."""
    target = Path(path).expanduser()
    guess = sniff(target)
    result = run(target, guess)
    fp = fingerprint(guess, result["analyzers"])
    return {
        "path": result["path"],
        "format": result["format"],
        "fingerprint": fp,
        "analyzers": result["analyzers"],
    }
