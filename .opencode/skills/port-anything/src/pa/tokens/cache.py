"""Content-addressed cache for token estimates and context plans.

Keys are SHA-256 digests of a kind plus a payload, so a stale entry can never
be returned for different content. Writes are atomic: a partial file is never
visible to a reader.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..util.hash import sha256_bytes
from ..util.paths import CACHE_ROOT

CACHE_DIR = CACHE_ROOT / "tokens"


def cache_dir() -> Path:
    directory = CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def make_key(kind: str, payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return sha256_bytes(kind.encode("utf-8") + b"\0" + data)


def _entry_path(key: str) -> Path:
    return cache_dir() / key[:2] / f"{key}.json"


def get(kind: str, payload: bytes | str) -> Any | None:
    path = _entry_path(make_key(kind, payload))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("kind") != kind:
        return None
    return data.get("value")


def put(kind: str, payload: bytes | str, value: Any) -> str:
    key = make_key(kind, payload)
    path = _entry_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {"kind": kind, "created": int(time.time()), "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".tmp-", delete=False
    )
    try:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
    return key


def memo(kind: str, payload: bytes | str, producer: Callable[[], Any]) -> Any:
    cached = get(kind, payload)
    if cached is not None:
        return cached
    value = producer()
    put(kind, payload, value)
    return value


def stats() -> dict[str, Any]:
    directory = cache_dir()
    entries = 0
    total = 0
    for path in directory.rglob("*.json"):
        try:
            total += path.stat().st_size
            entries += 1
        except OSError:
            continue
    return {"dir": str(directory), "entries": entries, "bytes": total}


def clear() -> int:
    directory = cache_dir()
    removed = 0
    for path in directory.rglob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
