"""File format sniffing by magic bytes and container inspection.

Returns an explicit ``unknown`` guess when nothing matches. Confidence is part
of the result so downstream code can distinguish a certain PE from a guess.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HEAD_BYTES = 8192
TAIL_BYTES = 4096


@dataclass
class FormatGuess:
    format_id: str
    family: str
    confidence: str
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.format_id != "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_id,
            "family": self.family,
            "confidence": self.confidence,
            "detail": self.detail,
            "known": self.known,
            **({"extra": self.extra} if self.extra else {}),
        }


def _read_head(path: Path, size: int = HEAD_BYTES) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _read_tail(path: Path, size: int = TAIL_BYTES) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        start = max(0, end - size)
        handle.seek(start)
        return handle.read(end - start)


def _pe_offset(head: bytes) -> int | None:
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    (e_lfanew,) = struct.unpack_from("<I", head, 0x3C)
    if e_lfanew + 4 > len(head) or head[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    return e_lfanew


def _zip_guess(path: Path) -> FormatGuess:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            lowered = {name.lower() for name in names}
            if "androidmanifest.xml" in lowered and (
                "classes.dex" in lowered or any(n.endswith(".dex") for n in lowered)
            ):
                return FormatGuess("apk", "android", "certain", "ZIP with AndroidManifest.xml and dex")
            if "bundleconfig.pb" in lowered or "base/manifest/androidmanifest.xml" in lowered:
                return FormatGuess("aab", "android", "certain", "ZIP with BundleConfig.pb")
            if "main.lua" in lowered:
                return FormatGuess("love_archive", "game", "certain", "ZIP with main.lua")
            if "unityfs" in lowered:
                return FormatGuess("unity_assetbundle", "game", "likely", "ZIP wrapping UnityFS data")
            return FormatGuess(
                "zip",
                "archive",
                "certain",
                f"ZIP archive with {len(names)} entries",
                {"entries": len(names)},
            )
    except (zipfile.BadZipFile, OSError):
        return FormatGuess("zip", "archive", "likely", "PK header but the central directory is unreadable")


def sniff(path: str | Path) -> FormatGuess:
    target = Path(path).expanduser()
    if target.is_dir():
        return FormatGuess("directory", "filesystem", "certain", "directory")
    if not target.is_file():
        return FormatGuess("missing", "filesystem", "certain", "path does not exist")
    try:
        head = _read_head(target)
    except OSError as exc:
        return FormatGuess("unreadable", "filesystem", "certain", f"cannot read: {exc}")
    if not head:
        return FormatGuess("empty", "filesystem", "certain", "zero-byte file")

    if head[:2] == b"MZ":
        offset = _pe_offset(head)
        if offset is not None:
            machine = struct.unpack_from("<H", head, offset + 4)[0] if offset + 6 <= len(head) else 0
            return FormatGuess("pe", "executable", "certain", f"PE signature at 0x{offset:x}", {"machine": machine})
        return FormatGuess("pe", "executable", "likely", "MZ header without a valid PE signature (DOS executable)")

    if head[:4] == b"\x7fELF":
        return FormatGuess("elf", "executable", "certain", "ELF magic")

    if len(head) >= 4:
        magic = head[:4]
        if magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
            return FormatGuess("macho", "executable", "certain", "Mach-O magic")
        if magic == b"\xca\xfe\xba\xbe":
            return FormatGuess("macho", "executable", "certain", "Mach-O universal binary")

    if head[:4] == b"dex\n":
        version = head[4:7].decode("ascii", errors="replace")
        return FormatGuess("dex", "android", "certain", f"DEX version {version}")

    if head[:2] == b"PK":
        return _zip_guess(target)

    if head[:7] == b"UnityFS":
        return FormatGuess("unity_assetbundle", "game", "certain", "UnityFS asset bundle")

    if head[:4] == b"\xaf\x1b\xb1\xfa":
        version = struct.unpack_from("<I", head, 4)[0] if len(head) >= 8 else 0
        return FormatGuess(
            "unity_global_metadata",
            "game",
            "certain",
            "IL2CPP global-metadata.dat",
            {"metadata_version": version},
        )

    if len(head) >= 4 and struct.unpack_from("<I", head, 0)[0] == 0xC1832A9E:
        return FormatGuess("ue_uasset", "game", "certain", "Unreal package tag 0xC1832A9E")

    if head[:4] == b"GDPC":
        return FormatGuess("godot_pck", "game", "certain", "Godot PCK magic")

    if head[:4] == b"FORM" and head[8:12] == b"GEN8":
        return FormatGuess("gamemaker_datawin", "game", "certain", "GameMaker GEN8 data")

    if head[:4] == b"RPA-":
        version = head[4:7].decode("ascii", errors="replace")
        return FormatGuess("renpy_rpa", "game", "certain", f"Ren'Py RPA {version}")

    if len(head) >= 5 and head[:5] == b"\0asm":
        return FormatGuess("wasm", "bytecode", "certain", "WebAssembly module")

    if head[:16] == b"SQLite format 3\x00":
        return FormatGuess("sqlite", "database", "certain", "SQLite database")

    if head[:4] == b"OggS":
        return FormatGuess("ogg", "audio", "certain", "Ogg container")

    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return FormatGuess("png", "image", "certain", "PNG image")

    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return FormatGuess("wav", "audio", "certain", "RIFF WAVE audio")

    if head[:4] in (b"DDS ",):
        return FormatGuess("dds", "image", "certain", "DirectDraw Surface texture")

    if head[:7] == b"#!AMF\x00" or head[:4] == b"\x28\xb5\x2f\xfd":
        return FormatGuess("unknown", "unknown", "possible", "compressed or packed payload")

    try:
        tail = _read_tail(target)
        if b"\xe1\x12\x6f\x5a" in tail:
            return FormatGuess("ue_pak", "game", "likely", "Unreal pak footer magic near EOF")
        if target.suffix.lower() in {".iso", ".img"} and len(head) > 0x8006 and head[0x8001:0x8006] == b"CD001":
            return FormatGuess("iso9660", "disc", "certain", "ISO 9660 primary volume descriptor")
    except OSError:
        pass

    try:
        with target.open("rb") as handle:
            handle.seek(0x8001)
            if handle.read(5) == b"CD001":
                return FormatGuess("iso9660", "disc", "certain", "ISO 9660 primary volume descriptor")
    except OSError:
        pass

    return FormatGuess("unknown", "unknown", "certain", f"no known magic in first {len(head)} bytes")
