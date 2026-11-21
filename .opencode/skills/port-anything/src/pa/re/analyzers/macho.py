"""Mach-O analyzer, including fat/universal binaries."""

from __future__ import annotations

import struct
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

CPUS = {
    7: "x86",
    0x01000007: "x86-64",
    12: "ARM",
    0x0100000C: "ARM64",
    18: "PowerPC",
    0x01000012: "PowerPC64",
}

FILETYPES = {
    1: "object",
    2: "execute",
    6: "dylib",
    8: "bundle",
    10: "dSYM",
}

MAGICS = {
    b"\xce\xfa\xed\xfe": (32, "<"),
    b"\xfe\xed\xfa\xce": (32, ">"),
    b"\xcf\xfa\xed\xfe": (64, "<"),
    b"\xfe\xed\xfa\xcf": (64, ">"),
}

FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": (32, ">"),
    b"\xbe\xba\xfe\xca": (32, "<"),
    b"\xca\xfe\xba\xbf": (64, ">"),
    b"\xbf\xba\xfe\xca": (64, "<"),
}

LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_UUID = 0x1B
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_MACOSX = 0x24
LC_VERSION_MIN_IPHONEOS = 0x25
LC_MAIN = 0x80000028

PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "maccatalyst", 7: "iossimulator"}


def _cstring(data: bytes, offset: int, limit: int = 512) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", errors="replace")


def _version(value: int) -> str:
    return f"{value >> 16}.{(value >> 8) & 0xFF}.{value & 0xFF}"


def _parse_slice(data: bytes, offset: int, bits: int, endian: str) -> dict[str, Any]:
    base = offset
    cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags = struct.unpack_from(
        endian + "iiIIII", data, base + 4
    )
    header_size = 32 if bits == 64 else 28
    cursor = base + header_size
    load_commands: list[dict[str, Any]] = []
    dylibs: list[str] = []
    segments: list[str] = []
    build: dict[str, Any] = {}
    entry = None
    uuid = None

    for _ in range(min(ncmds, 4096)):
        if cursor + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from(endian + "II", data, cursor)
        if cmdsize < 8 or cursor + cmdsize > len(data):
            break
        command: dict[str, Any] = {"cmd": cmd, "cmdsize": cmdsize}
        if cmd == LC_SEGMENT_64:
            segname = data[cursor + 8 : cursor + 24].rstrip(b"\0").decode("ascii", errors="replace")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(endian + "QQQQ", data, cursor + 24)
            command.update({"segname": segname, "vmaddr": vmaddr, "vmsize": vmsize, "fileoff": fileoff, "filesize": filesize})
            segments.append(segname)
        elif cmd == LC_SEGMENT:
            segname = data[cursor + 8 : cursor + 24].rstrip(b"\0").decode("ascii", errors="replace")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(endian + "IIII", data, cursor + 24)
            command.update({"segname": segname, "vmaddr": vmaddr, "vmsize": vmsize, "fileoff": fileoff, "filesize": filesize})
            segments.append(segname)
        elif cmd in (LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            name_offset = struct.unpack_from(endian + "I", data, cursor + 8)[0]
            name = _cstring(data, cursor + name_offset)
            command["name"] = name
            if cmd != LC_ID_DYLIB:
                dylibs.append(name)
        elif cmd == LC_BUILD_VERSION:
            platform, minos, sdk, ntools = struct.unpack_from(endian + "IIII", data, cursor + 8)
            build = {"platform": PLATFORMS.get(platform, str(platform)), "minos": _version(minos), "sdk": _version(sdk), "tools": ntools}
        elif cmd in (LC_VERSION_MIN_MACOSX, LC_VERSION_MIN_IPHONEOS):
            version, sdk = struct.unpack_from(endian + "II", data, cursor + 8)
            build = {
                "platform": "macos" if cmd == LC_VERSION_MIN_MACOSX else "ios",
                "minos": _version(version),
                "sdk": _version(sdk),
            }
        elif cmd == LC_UUID:
            uuid = data[cursor + 8 : cursor + 24].hex()
        elif cmd == LC_MAIN:
            entry = struct.unpack_from(endian + "Q", data, cursor + 8)[0]
        load_commands.append(command)
        cursor += cmdsize

    return {
        "bits": bits,
        "cputype": CPUS.get(cputype, f"0x{cputype:x}"),
        "cputype_id": cputype,
        "filetype": FILETYPES.get(filetype, str(filetype)),
        "filetype_id": filetype,
        "flags": flags,
        "load_commands": load_commands,
        "dylibs": dylibs,
        "segments": segments,
        "build": build,
        "entry": entry,
        "uuid": uuid,
    }


class MachOAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="macho",
        name="Mach-O parser",
        formats=("macho",),
        inputs=("file",),
        output_schema="macho.v1",
        dependencies=(),
        limitations=(
            "Code signatures are not verified.",
            "Only the first 4096 load commands are read.",
            "Fat slices are described but not fully parsed beyond their headers.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "macho"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        data = context.path.read_bytes()[: 32 * 1024 * 1024]
        magic = data[:4]
        if magic in MAGICS:
            bits, endian = MAGICS[magic]
            parsed = _parse_slice(data, 0, bits, endian)
            return {"fat": False, **parsed, "size": len(data)}

        if magic in FAT_MAGICS:
            width, endian = FAT_MAGICS[magic]
            nfat = struct.unpack_from(endian + "I", data, 4)[0]
            slices = []
            entry_size = 32 if width == 64 else 20
            for index in range(min(nfat, 64)):
                base = 8 + index * entry_size
                if base + entry_size > len(data):
                    break
                if width == 64:
                    cputype, cpusubtype, offset, size, align, _reserved = struct.unpack_from(
                        endian + "iiQQII", data, base
                    )
                else:
                    cputype, cpusubtype, offset, size, align = struct.unpack_from(
                        endian + "iiIII", data, base
                    )
                if offset + 4 > len(data):
                    continue
                slice_magic = data[offset : offset + 4]
                if slice_magic not in MAGICS:
                    slices.append({"cputype": CPUS.get(cputype, hex(cputype)), "offset": offset, "size": size, "error": "unknown slice magic"})
                    continue
                slice_bits, slice_endian = MAGICS[slice_magic]
                parsed = _parse_slice(data, offset, slice_bits, slice_endian)
                parsed.update({"offset": offset, "size": size, "align": align})
                slices.append(parsed)
            return {"fat": True, "slice_count": len(slices), "slices": slices, "size": len(data)}

        raise ValueError("not a Mach-O file")


register(MachOAnalyzer())
