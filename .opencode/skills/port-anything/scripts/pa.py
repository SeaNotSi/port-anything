#!/usr/bin/env python3
"""Skill-local launcher: same behaviour as bin/pa, resolved from scripts/."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent / "src"))

from pa.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
