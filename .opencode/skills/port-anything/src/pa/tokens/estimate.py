"""Token estimation without a model-specific tokenizer.

The estimate is a documented heuristic: ASCII non-space characters cost
``1 / chars_per_token`` tokens, CJK characters cost one token each. It is
accurate enough for budgeting and never claims to be exact. Binary files are
flagged, not silently counted as text.
"""

from __future__ import annotations

import codecs
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import load as load_config

METHOD = "heuristic-v1"
CHUNK = 1 << 20

CJK_RANGES = (
    (0x1100, 0x11FF),
    (0x2E80, 0x303F),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE4F),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)


@dataclass
class Estimate:
    tokens: int
    chars: int
    lines: int
    bytes: int
    complete: bool
    method: str = METHOD
    confidence: str = "±20%"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "chars": self.chars,
            "lines": self.lines,
            "bytes": self.bytes,
            "complete": self.complete,
            "method": self.method,
            "confidence": self.confidence,
        }


def is_cjk(character: str) -> bool:
    code = ord(character)
    for low, high in CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def count_text(text: str, chars_per_token: float = 4.0, cjk_chars_per_token: float = 1.0) -> Estimate:
    """Estimate tokens for an in-memory string."""
    ascii_chars = 0
    cjk_chars = 0
    lines = text.count("\n")
    if text and not text.endswith("\n"):
        lines += 1
    for character in text:
        if character.isspace():
            continue
        if is_cjk(character):
            cjk_chars += 1
        else:
            ascii_chars += 1
    tokens = math.ceil(ascii_chars / chars_per_token) + math.ceil(cjk_chars / cjk_chars_per_token)
    confidence = "±10%" if cjk_chars == 0 else "±20%"
    return Estimate(
        tokens=tokens,
        chars=len(text),
        lines=lines,
        bytes=len(text.encode("utf-8", errors="replace")),
        complete=True,
        confidence=confidence,
    )


def _looks_binary(head: bytes) -> bool:
    return b"\0" in head


def estimate_file(path: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stream a file and estimate its token count. Binary files are flagged."""
    cfg = config or load_config()
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(str(target))
    size = target.stat().st_size
    limit = int(cfg["max_file_bytes"])
    chars_per_token = float(cfg["chars_per_token"])
    cjk_chars_per_token = float(cfg["cjk_chars_per_token"])

    ascii_chars = 0
    cjk_chars = 0
    chars = 0
    lines = 0
    read = 0
    binary = False
    last_char = ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with target.open("rb") as handle:
        head = handle.read(4096)
        binary = _looks_binary(head)
        handle.seek(0)
        while read < min(size, limit):
            block = handle.read(min(CHUNK, limit - read))
            if not block:
                break
            read += len(block)
            text = decoder.decode(block, final=read >= size)
            chars += len(text)
            lines += text.count("\n")
            if text:
                last_char = text[-1]
            for character in text:
                if character.isspace():
                    continue
                if is_cjk(character):
                    cjk_chars += 1
                else:
                    ascii_chars += 1
        tail = decoder.decode(b"", final=True)
        chars += len(tail)
        lines += tail.count("\n")
        if tail:
            last_char = tail[-1]

    if chars and last_char != "\n":
        lines += 1
    tokens = math.ceil(ascii_chars / chars_per_token) + math.ceil(cjk_chars / cjk_chars_per_token)
    complete = size <= limit
    return {
        "path": str(target),
        "bytes": size,
        "read_bytes": read,
        "chars": chars,
        "lines": lines,
        "tokens": tokens,
        "complete": complete,
        "binary": binary,
        "method": METHOD,
        "confidence": "±10%" if cjk_chars == 0 else "±20%",
    }


def estimate_paths(paths: Iterable[str | Path], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    files = [estimate_file(path, cfg) for path in paths]
    return {
        "files": files,
        "total_tokens": sum(item["tokens"] for item in files),
        "total_bytes": sum(item["bytes"] for item in files),
        "incomplete": [item["path"] for item in files if not item["complete"]],
        "method": METHOD,
    }
