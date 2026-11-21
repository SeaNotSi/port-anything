"""ELF analyzer: header, sections, program headers and dynamic dependencies."""

from __future__ import annotations

import struct
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register

MACHINES = {
    0x03: "x86",
    0x3E: "x86-64",
    0x28: "ARM",
    0xB7: "AArch64",
    0x08: "MIPS",
    0x14: "PowerPC",
    0x15: "PowerPC64",
    0x16: "S390",
    0xF3: "RISC-V",
    0x32: "IA64",
}

TYPES = {
    0: "none",
    1: "relocatable",
    2: "executable",
    3: "shared-object",
    4: "core",
}

DYNAMIC_TAGS = {
    1: "NEEDED",
    14: "SONAME",
    15: "RPATH",
    29: "RUNPATH",
}


def _u16(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "H", data, offset)[0]


def _u32(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "I", data, offset)[0]


def _u64(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack_from(endian + "Q", data, offset)[0]


def _cstring(data: bytes, offset: int, limit: int = 512) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", errors="replace")


class ELFAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="elf",
        name="ELF parser",
        formats=("elf",),
        inputs=("file",),
        output_schema="elf.v1",
        dependencies=(),
        limitations=(
            "Stripped binaries have no symbol table.",
            "Only the first 64 KiB of section names and dynamic strings are read.",
            "Android bionic detection is heuristic, based on section and library names.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "elf"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(min(size, 64 * 1024 * 1024))
        if data[:4] != b"\x7fELF":
            raise ValueError("not an ELF file")
        elf_class = data[4]
        encoding = data[5]
        endian = "<" if encoding == 1 else ">"
        bits = 64 if elf_class == 2 else 32

        if bits == 64:
            e_type = _u16(data, 16, endian)
            e_machine = _u16(data, 18, endian)
            e_version = _u32(data, 20, endian)
            e_entry = _u64(data, 24, endian)
            e_phoff = _u64(data, 32, endian)
            e_shoff = _u64(data, 40, endian)
            e_flags = _u32(data, 48, endian)
            e_ehsize = _u16(data, 52, endian)
            e_phentsize = _u16(data, 54, endian)
            e_phnum = _u16(data, 56, endian)
            e_shentsize = _u16(data, 58, endian)
            e_shnum = _u16(data, 60, endian)
            e_shstrndx = _u16(data, 62, endian)
        else:
            e_type = _u16(data, 16, endian)
            e_machine = _u16(data, 18, endian)
            e_version = _u32(data, 20, endian)
            e_entry = _u32(data, 24, endian)
            e_phoff = _u32(data, 28, endian)
            e_shoff = _u32(data, 32, endian)
            e_flags = _u32(data, 36, endian)
            e_ehsize = _u16(data, 40, endian)
            e_phentsize = _u16(data, 42, endian)
            e_phnum = _u16(data, 44, endian)
            e_shentsize = _u16(data, 46, endian)
            e_shnum = _u16(data, 48, endian)
            e_shstrndx = _u16(data, 50, endian)

        sections: list[dict[str, Any]] = []
        if e_shoff and e_shnum and e_shentsize:
            count = min(e_shnum, 512)
            raw_sections = []
            for index in range(count):
                base = e_shoff + index * e_shentsize
                if base + e_shentsize > len(data):
                    break
                if bits == 64:
                    name, stype = _u32(data, base, endian), _u32(data, base + 4, endian)
                    flags = _u64(data, base + 8, endian)
                    addr = _u64(data, base + 16, endian)
                    offset = _u64(data, base + 24, endian)
                    sec_size = _u64(data, base + 32, endian)
                    link = _u32(data, base + 40, endian)
                else:
                    name, stype = _u32(data, base, endian), _u32(data, base + 4, endian)
                    flags = _u32(data, base + 8, endian)
                    addr = _u32(data, base + 12, endian)
                    offset = _u32(data, base + 16, endian)
                    sec_size = _u32(data, base + 20, endian)
                    link = _u32(data, base + 24, endian)
                raw_sections.append(
                    {
                        "name_offset": name,
                        "type": stype,
                        "flags": flags,
                        "address": addr,
                        "offset": offset,
                        "size": sec_size,
                        "link": link,
                    }
                )
            shstrtab_offset = 0
            if e_shstrndx < len(raw_sections):
                shstrtab_offset = raw_sections[e_shstrndx]["offset"]
            for section in raw_sections:
                section["name"] = _cstring(data, shstrtab_offset + section["name_offset"], 128)
                section.pop("name_offset", None)
            sections = raw_sections

        program_headers: list[dict[str, Any]] = []
        if e_phoff and e_phnum and e_phentsize:
            for index in range(min(e_phnum, 128)):
                base = e_phoff + index * e_phentsize
                if base + e_phentsize > len(data):
                    break
                p_type = _u32(data, base, endian)
                if bits == 64:
                    p_flags = _u32(data, base + 4, endian)
                    p_offset = _u64(data, base + 8, endian)
                    p_vaddr = _u64(data, base + 16, endian)
                    p_filesz = _u64(data, base + 32, endian)
                    p_memsz = _u64(data, base + 40, endian)
                else:
                    p_offset = _u32(data, base + 4, endian)
                    p_vaddr = _u32(data, base + 8, endian)
                    p_filesz = _u32(data, base + 16, endian)
                    p_memsz = _u32(data, base + 20, endian)
                    p_flags = _u32(data, base + 24, endian)
                program_headers.append(
                    {
                        "type": p_type,
                        "flags": p_flags,
                        "offset": p_offset,
                        "vaddr": p_vaddr,
                        "filesz": p_filesz,
                        "memsz": p_memsz,
                    }
                )

        dynamic = [ph for ph in program_headers if ph["type"] == 2]
        needed: list[str] = []
        soname = None
        rpath: list[str] = []
        runpath: list[str] = []
        if dynamic:
            dyn = dynamic[0]
            base = dyn["offset"]
            count = min(dyn["filesz"] // (16 if bits == 64 else 8), 4096)
            strtab_addr = 0
            entries = []
            for index in range(count):
                cursor = base + index * (16 if bits == 64 else 8)
                if cursor + (16 if bits == 64 else 8) > len(data):
                    break
                if bits == 64:
                    tag, val = _u64(data, cursor, endian), _u64(data, cursor + 8, endian)
                else:
                    tag, val = _u32(data, cursor, endian), _u32(data, cursor + 4, endian)
                entries.append((tag, val))
                if tag == 0:
                    break
                if tag == 5:
                    strtab_addr = val
            for tag, val in entries:
                if tag == 5:
                    continue
                if tag in DYNAMIC_TAGS:
                    # Translate the string table virtual address to a file offset.
                    offset = None
                    for ph in program_headers:
                        if ph["type"] == 1 and ph["vaddr"] <= strtab_addr < ph["vaddr"] + ph["filesz"]:
                            offset = ph["offset"] + (strtab_addr - ph["vaddr"]) + val
                            break
                    text = _cstring(data, offset) if offset is not None else ""
                    if not text:
                        continue
                    if tag == 1:
                        needed.append(text)
                    elif tag == 14:
                        soname = text
                    elif tag == 15:
                        rpath.append(text)
                    elif tag == 29:
                        runpath.append(text)

        section_names = {section["name"] for section in sections}
        is_android = any(
            marker in " ".join(needed)
            for marker in ("liblog.so", "libandroid.so", "libnativewindow.so")
        ) or any(name.startswith(".note.android") for name in section_names)

        return {
            "size": size,
            "bits": bits,
            "endianness": "little" if encoding == 1 else "big",
            "osabi": data[7],
            "type": TYPES.get(e_type, str(e_type)),
            "type_id": e_type,
            "machine": MACHINES.get(e_machine, f"0x{e_machine:x}"),
            "machine_id": e_machine,
            "entry": e_entry,
            "flags": e_flags,
            "sections": sections,
            "section_names": sorted(section_names),
            "program_headers": program_headers,
            "needed": needed,
            "soname": soname,
            "rpath": rpath,
            "runpath": runpath,
            "is_pie": e_type == 3 and any(ph["type"] == 3 for ph in program_headers),
            "is_stripped": ".symtab" not in section_names,
            "is_android": is_android,
        }


register(ELFAnalyzer())
