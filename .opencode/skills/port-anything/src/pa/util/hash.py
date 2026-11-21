"""Hashing helpers shared by the token and source modules."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def sha256_file(path: str | Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    read = 0
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            if limit is not None:
                remaining = limit - read
                if remaining <= 0:
                    break
                block = block[:remaining]
            digest.update(block)
            read += len(block)
    return digest.hexdigest()
