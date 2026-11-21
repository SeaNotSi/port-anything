"""PE / PE32+ analyzer.

Parses the DOS header, COFF header, optional header, section table, import
directory, CLR directory and debug directory. All offsets are bounds-checked.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

MACHINES = {
    0x014C: "i386",
    0x8664: "x86-64",
    0x01C0: "ARM",
    0x01C4: "ARMv7 (Thumb-2)",
    0xAA64: "ARM64",
    0x0200: "IA64",
    0x5032: "RISC-V 32",
    0x5064: "RISC-V 64",
}

SUBSYSTEMS = {
    1: "native",
    2: "windows-gui",
    3: "windows-cui",
    5: "os2-cui",
    7: "posix-cui",
    9: "windows-ce",
    10: "efi-application",
    14: "xbox",
    16: "windows-boot",
}

MAX_SECTIONS = 96
MAX_IMPORT_DESCRIPTORS = 512
MAX_THUNKS = 4096


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("read past end")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise ValueError("read past end")
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    if offset + 8 > len(data):
        raise ValueError("read past end")
    return struct.unpack_from("<Q", data, offset)[0]


def _cstring(data: bytes, offset: int, limit: int = 256) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", errors="replace")


class _RvaMapper:
    def __init__(self, sections: list[dict[str, Any]]):
        self.sections = sections

    def to_offset(self, rva: int) -> int | None:
        for section in self.sections:
            start = section["virtual_address"]
            span = max(section["virtual_size"], section["raw_size"])
            if start <= rva < start + span:
                delta = rva - start
                if delta >= section["raw_size"]:
                    return None
                return section["raw_pointer"] + delta
        return None


class PEAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="pe",
        name="PE / PE32+ parser",
        formats=("pe",),
        inputs=("file",),
        output_schema="pe.v1",
        dependencies=(),
        limitations=(
            "Packed binaries hide imports; the import table may be empty.",
            "Only the first 512 import descriptors and 4096 thunks are read.",
            "Managed .NET metadata is detected but not decompiled.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "pe"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(0x400)
        e_lfanew = _u32(header, 0x3C)
        if e_lfanew + 24 > len(header):
            raise ValueError(f"e_lfanew 0x{e_lfanew:x} outside the read window")
        coff = e_lfanew + 4
        machine = _u16(header, coff)
        number_of_sections = _u16(header, coff + 2)
        timestamp = _u32(header, coff + 4)
        optional_size = _u16(header, coff + 16)
        characteristics = _u16(header, coff + 18)
        optional = coff + 20
        magic = _u16(header, optional)
        pe32_plus = magic == 0x20B
        if magic not in (0x10B, 0x20B):
            raise ValueError(f"unknown optional header magic 0x{magic:x}")
        entry_point = _u32(header, optional + 16)
        image_base = _u64(header, optional + 24) if pe32_plus else _u32(header, optional + 28)
        subsystem = _u16(header, optional + 68)
        dll_characteristics = _u16(header, optional + 70)
        number_of_rva = _u32(header, optional + (108 if pe32_plus else 92))
        dirs_base = optional + (112 if pe32_plus else 96)

        with path.open("rb") as handle:
            handle.seek(0)
            data = handle.read(min(size, 32 * 1024 * 1024))
        sections: list[dict[str, Any]] = []
        section_table = optional + optional_size
        for index in range(min(number_of_sections, MAX_SECTIONS)):
            base = section_table + index * 40
            if base + 40 > len(data):
                break
            raw_name = data[base : base + 8].rstrip(b"\0")
            sections.append(
                {
                    "name": raw_name.decode("ascii", errors="replace"),
                    "virtual_size": _u32(data, base + 8),
                    "virtual_address": _u32(data, base + 12),
                    "raw_size": _u32(data, base + 16),
                    "raw_pointer": _u32(data, base + 20),
                    "characteristics": _u32(data, base + 36),
                }
            )

        directories: list[dict[str, int]] = []
        for index in range(min(number_of_rva, 16)):
            base = dirs_base + index * 8
            if base + 8 > len(data):
                break
            directories.append({"index": index, "rva": _u32(data, base), "size": _u32(data, base + 4)})

        mapper = _RvaMapper(sections)
        imports: list[dict[str, Any]] = []
        import_dir = next((d for d in directories if d["index"] == 1), None)
        if import_dir and import_dir["rva"]:
            offset = mapper.to_offset(import_dir["rva"])
            if offset is not None:
                for index in range(MAX_IMPORT_DESCRIPTORS):
                    base = offset + index * 20
                    if base + 20 > len(data):
                        break
                    name_rva = _u32(data, base + 12)
                    first_thunk = _u32(data, base + 16)
                    if name_rva == 0 and first_thunk == 0:
                        break
                    name_offset = mapper.to_offset(name_rva)
                    dll = _cstring(data, name_offset) if name_offset is not None else ""
                    functions: list[str] = []
                    thunk_offset = mapper.to_offset(first_thunk) if first_thunk else None
                    if thunk_offset is not None:
                        step = 8 if pe32_plus else 4
                        for thunk_index in range(MAX_THUNKS):
                            cursor = thunk_offset + thunk_index * step
                            if cursor + step > len(data):
                                break
                            value = _u64(data, cursor) if pe32_plus else _u32(data, cursor)
                            if value == 0:
                                break
                            if value & (1 << (63 if pe32_plus else 31)):
                                ordinal = value & 0xFFFF
                                functions.append(f"#{ordinal}")
                            else:
                                hint_name = mapper.to_offset(value & 0x7FFFFFFF)
                                if hint_name is None:
                                    break
                                functions.append(_cstring(data, hint_name + 2))
                    imports.append({"dll": dll, "functions": functions[:64], "function_count": len(functions)})

        clr_dir = next((d for d in directories if d["index"] == 14), None)
        debug_dir = next((d for d in directories if d["index"] == 6), None)
        pdb_path = None
        if debug_dir and debug_dir["rva"]:
            offset = mapper.to_offset(debug_dir["rva"])
            if offset is not None and offset + 28 <= len(data):
                _size, _rva, _raw, _ptr = struct.unpack_from("<IIII", data, offset)
                if offset + 24 + 4 <= len(data):
                    cv_size = _u32(data, offset + 16)
                    cv_ptr = _u32(data, offset + 24)
                    if cv_size and cv_ptr + 24 <= len(data) and data[cv_ptr : cv_ptr + 4] == b"RSDS":
                        pdb_path = _cstring(data, cv_ptr + 24, 260)

        return {
            "size": size,
            "machine": MACHINES.get(machine, f"0x{machine:04x}"),
            "machine_id": machine,
            "pe32_plus": pe32_plus,
            "is_dll": bool(characteristics & 0x2000),
            "is_system": bool(characteristics & 0x1000),
            "timestamp": timestamp,
            "subsystem": SUBSYSTEMS.get(subsystem, str(subsystem)),
            "dll_characteristics": dll_characteristics,
            "entry_point_rva": entry_point,
            "image_base": image_base,
            "sections": sections,
            "imports": imports,
            "imported_dlls": [entry["dll"] for entry in imports if entry["dll"]],
            "is_dotnet": bool(clr_dir and clr_dir["rva"]),
            "pdb_path": pdb_path,
        }


register(PEAnalyzer())
