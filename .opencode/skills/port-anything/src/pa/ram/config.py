"""Configuration for the RAM-efficiency module.

Defaults are in code so the module still works when installed without its
config directory. A JSON file overrides them; unknown keys are dropped and a
bad value falls back to the default rather than raising. A negative or zero
threshold is never accepted: a zero reserve would let a build eat the device.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..util.paths import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "ram-efficiency.json"

DEFAULTS: dict[str, Any] = {
    "low_available_ratio": 0.10,
    "medium_available_ratio": 0.25,
    "low_available_bytes": 512 * 1024 * 1024,
    "min_free_bytes": 384 * 1024 * 1024,
    "concurrency": {"low": 1, "medium": 2, "high": 3},
    "max_concurrency": 8,
    "history_samples": 120,
    "sample_interval_seconds": 1.0,
    "reserve_cpu_factor": 0.5,
}

LEVELS = ("low", "medium", "high")


def _valid_scalar(key: str, value: Any) -> bool:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return isinstance(value, type(default))


def _valid_concurrency(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) != set(LEVELS):
        return False
    return all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 1
        for item in value.values()
    )


def load(path: str | Path | None = None) -> dict[str, Any]:
    config = {**DEFAULTS, "concurrency": dict(DEFAULTS["concurrency"])}
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
        if key == "concurrency":
            if _valid_concurrency(value):
                config["concurrency"] = {level: int(value[level]) for level in LEVELS}
            continue
        if key in DEFAULTS and _valid_scalar(key, value):
            config[key] = value
    # Ratios must be ordered, otherwise the medium band could sit under low.
    if config["medium_available_ratio"] <= config["low_available_ratio"]:
        config["medium_available_ratio"] = DEFAULTS["medium_available_ratio"]
        config["low_available_ratio"] = DEFAULTS["low_available_ratio"]
    if config["low_available_bytes"] <= config["min_free_bytes"]:
        config["low_available_bytes"] = DEFAULTS["low_available_bytes"]
    if not 0.0 < config["reserve_cpu_factor"] < 1.0:
        config["reserve_cpu_factor"] = DEFAULTS["reserve_cpu_factor"]
    return config