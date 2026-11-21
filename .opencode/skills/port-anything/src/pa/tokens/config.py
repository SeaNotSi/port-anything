"""Configuration for the token-efficiency module.

Defaults live in code so the module works when installed without its data
directory. A JSON file overrides them; unknown keys are ignored and bad values
fall back to the default instead of raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..util.paths import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "token-efficiency.json"

DEFAULTS: dict[str, Any] = {
    "chars_per_token": 4.0,
    "cjk_chars_per_token": 1.0,
    "max_file_bytes": 64 * 1024 * 1024,
    "context_default_budget": 32000,
    "chunk_lines": 40,
    "head_lines": 30,
    "tail_lines": 20,
    "max_files": 20000,
    "cache_enabled": True,
    "duplicate_near_threshold": 0.85,
}


def _valid(key: str, value: Any) -> bool:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return isinstance(value, type(default))


def load(path: str | Path | None = None) -> dict[str, Any]:
    config = dict(DEFAULTS)
    target = Path(path) if path else CONFIG_FILE
    if not target.is_file():
        return config
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(data, dict):
        return config
    for key, value in data.items():
        if key in DEFAULTS and _valid(key, value):
            config[key] = value
    return config
