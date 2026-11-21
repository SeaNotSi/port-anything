"""Content-addressed cache for intermediate build artifacts.

An Android port produces the same object files over and over — a C++ source set
recompiles identically between attempts. The cache keeps those outputs on disk
under a content hash and drops the oldest entries when the device needs room.

Nothing in here runs ``rm`` on a path it did not create. Eviction is confined to
the cache directory and every read is hash-verified, so a truncated or corrupted
entry is treated as a miss rather than a corrupt object file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from ..util.paths import CACHE_ROOT

CACHE_DIR = CACHE_ROOT / "ram-cache"
# Keep at least this much space free on the filesystem holding the cache.
MIN_FREE_BYTES = 384 * 1024 * 1024
DEFAULT_MAX_BYTES = 4 * 1024 * 1024 * 1024


def cache_dir(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    override = os.environ.get("PA_RAM_CACHE")
    if override:
        return Path(override).expanduser()
    return CACHE_DIR


def key_of(*parts: str | bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        raw = part if isinstance(part, bytes) else part.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _entry_paths(directory: Path, key: str) -> tuple[Path, Path]:
    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError(f"cache key must be a 64-char lowercase hex digest: {key!r}")
    return directory / f"{key}.blob", directory / f"{key}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    handle, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def put(
    data: bytes,
    *,
    key: str | None = None,
    root: str | Path | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store *data*, returning the entry descriptor. Idempotent for a given key."""
    directory = cache_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    digest = key or hashlib.sha256(data).hexdigest()
    blob, sidecar = _entry_paths(directory, digest)
    if blob.is_file() and sidecar.is_file():
        try:
            existing = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and existing.get("bytes") == len(data):
            return {**existing, "hit": True}
    _atomic_write(blob, data)
    record = {
        "key": digest,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "created": round(time.time(), 3),
        "meta": meta or {},
    }
    _atomic_write(sidecar, json.dumps(record, sort_keys=True).encode("utf-8"))
    return {**record, "hit": False}


def get(key: str, *, root: str | Path | None = None) -> bytes | None:
    """Return verified bytes, or ``None`` for a missing or corrupt entry."""
    directory = cache_dir(root)
    try:
        blob, sidecar = _entry_paths(directory, key)
    except ValueError:
        return None
    if not blob.is_file():
        return None
    try:
        data = blob.read_bytes()
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("bytes") != len(data):
        return None
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        return None
    return data


def has(key: str, *, root: str | Path | None = None) -> bool:
    return get(key, root=root) is not None


def entries(root: str | Path | None = None) -> list[dict[str, Any]]:
    directory = cache_dir(root)
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for sidecar in sorted(directory.glob("*.json")):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
            stat = sidecar.stat()
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        found.append(
            {
                "key": record.get("key", sidecar.stem),
                "bytes": int(record.get("bytes") or 0),
                "created": record.get("created"),
                "mtime": stat.st_mtime,
                "meta": record.get("meta") or {},
            }
        )
    return found


def stats(root: str | Path | None = None) -> dict[str, Any]:
    directory = cache_dir(root)
    items = entries(root)
    return {
        "path": str(directory),
        "exists": directory.is_dir(),
        "entries": len(items),
        "bytes": sum(item["bytes"] for item in items),
        "oldest": min((item["mtime"] for item in items), default=None),
        "newest": max((item["mtime"] for item in items), default=None),
    }


def trim(max_bytes: int = DEFAULT_MAX_BYTES, *, root: str | Path | None = None) -> dict[str, Any]:
    """Evict oldest entries until the cache fits inside *max_bytes*."""
    directory = cache_dir(root)
    items = entries(root)
    total = sum(item["bytes"] for item in items)
    evicted: list[str] = []
    freed = 0
    for item in sorted(items, key=lambda entry: entry["mtime"]):
        if total <= max_bytes:
            break
        blob, sidecar = _entry_paths(directory, item["key"])
        for path in (blob, sidecar):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                break
        total -= item["bytes"]
        freed += item["bytes"]
        evicted.append(item["key"])
    return {
        "evicted": len(evicted),
        "keys": evicted,
        "freed_bytes": freed,
        "remaining_bytes": max(0, total),
        "max_bytes": max_bytes,
    }


def clear(root: str | Path | None = None) -> int:
    directory = cache_dir(root)
    if not directory.is_dir():
        return 0
    removed = 0
    for pattern in ("*.blob", "*.json", ".tmp-*"):
        for path in directory.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def free_space(path: str | Path | None = None) -> int:
    target = Path(path) if path else cache_dir()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def evict_for(
    required_bytes: int,
    *,
    root: str | Path | None = None,
    floor_bytes: int = MIN_FREE_BYTES,
) -> dict[str, Any]:
    """Make room for *required_bytes* by trimming the cache, never elsewhere."""
    directory = cache_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    free = free_space(directory)
    if free - int(required_bytes) >= int(floor_bytes):
        return {
            "ok": True,
            "required_bytes": int(required_bytes),
            "free_before": free,
            "free_after": free,
            "freed_bytes": 0,
            "evicted": 0,
        }
    need = int(required_bytes) + int(floor_bytes) - free
    current = sum(item["bytes"] for item in entries(root))
    result = trim(max(0, current - need), root=root)
    after = free_space(directory)
    return {
        "ok": after - int(required_bytes) >= int(floor_bytes),
        "required_bytes": int(required_bytes),
        "free_before": free,
        "free_after": after,
        "freed_bytes": result["freed_bytes"],
        "evicted": result["evicted"],
        "note": "" if after - int(required_bytes) >= int(floor_bytes) else "cache could not free enough space",
    }