"""Minimal structured logging. Writes to stderr; stdout stays machine-readable."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_LEVEL = LEVELS.get(os.environ.get("PA_LOG_LEVEL", "info").lower(), 20)


def _emit(level: str, message: str, **fields: Any) -> None:
    if LEVELS[level] < _LEVEL:
        return
    if os.environ.get("PA_LOG_JSON") == "1":
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": level,
            "msg": message,
            **fields,
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    else:
        suffix = " ".join(f"{k}={v}" for k, v in fields.items())
        print(f"[{level}] {message}{(' ' + suffix) if suffix else ''}", file=sys.stderr)


def debug(message: str, **fields: Any) -> None:
    _emit("debug", message, **fields)


def info(message: str, **fields: Any) -> None:
    _emit("info", message, **fields)


def warn(message: str, **fields: Any) -> None:
    _emit("warn", message, **fields)


def error(message: str, **fields: Any) -> None:
    _emit("error", message, **fields)
