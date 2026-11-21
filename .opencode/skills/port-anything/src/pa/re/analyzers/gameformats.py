"""Game asset container analyzers: Unity, Unreal, Godot, GameMaker, Ren'Py, LÖVE."""

from __future__ import annotations

import struct
import zipfile
from typing import Any

from ..registry import AnalysisContext, Analyzer, AnalyzerSpec, register


def _cstring(data: bytes, offset: int, limit: int = 256) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("utf-8", errors="replace")


class UnityBundleAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="unity_bundle",
        name="Unity AssetBundle header",
        formats=("unity_assetbundle",),
        inputs=("file",),
        output_schema="unity_bundle.v1",
        dependencies=("fileformat.unity_assetbundle",),
        limitations=(
            "Reads the container header only; serialized asset layout is version-specific.",
            "Compressed block data is not decompressed.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "unity_assetbundle"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        data = context.path.read_bytes()[: 4096]
        if data[:8] != b"UnityFS\0":
            raise ValueError("missing UnityFS magic")
        cursor = 8
        version = struct.unpack_from(">I", data, cursor)[0]
        cursor += 4
        unity_version = _cstring(data, cursor)
        cursor += len(unity_version) + 1
        revision = _cstring(data, cursor)
        cursor += len(revision) + 1
        size = struct.unpack_from(">q", data, cursor)[0]
        cursor += 8
        compressed_info, uncompressed_info, flags = struct.unpack_from(">III", data, cursor)
        compression = {0: "none", 1: "lzma", 2: "lz4", 3: "lz4hc"}.get(flags & 0x3F, f"0x{flags & 0x3F:x}")
        return {
            "format_version": version,
            "unity_version": unity_version,
            "unity_revision": revision,
            "bundle_size": size,
            "compressed_blocks_info_size": compressed_info,
            "uncompressed_blocks_info_size": uncompressed_info,
            "compression": compression,
            "flags": flags,
        }


class UnityMetadataAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="unity_metadata",
        name="IL2CPP global-metadata.dat header",
        formats=("unity_global_metadata",),
        inputs=("file",),
        output_schema="unity_metadata.v1",
        dependencies=("fileformat.unity_global_metadata",),
        limitations=(
            "Header only; table layout depends on the metadata version.",
            "Encrypted or obfuscated metadata is reported as unsupported.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "unity_global_metadata"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        data = context.path.read_bytes()[: 256]
        if data[:4] != b"\xaf\x1b\xb1\xfa":
            raise ValueError("bad IL2CPP metadata magic")
        version = struct.unpack_from("<I", data, 4)[0]
        string_offset, string_size = (None, None)
        if len(data) >= 16:
            string_offset, string_size = struct.unpack_from("<II", data, 8)
        plausible = version < 100 and string_offset is not None and 0 < string_offset < context.path.stat().st_size
        return {
            "metadata_version": version,
            "string_literal_offset": string_offset,
            "string_literal_size": string_size,
            "plausible_header": bool(plausible),
            "note": "metadata version maps to the Unity/IL2CPP release that produced it",
        }


class UnrealPackageAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="unreal_package",
        name="Unreal package header",
        formats=("ue_uasset",),
        inputs=("file",),
        output_schema="unreal_package.v1",
        dependencies=("fileformat.ue_uasset",),
        limitations=(
            "Header fields only; cooked property layout requires the matching engine version.",
            "Encrypted packages are out of scope.",
        ),
        verification="partially_verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "ue_uasset"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        data = context.path.read_bytes()[: 4096]
        tag = struct.unpack_from("<I", data, 0)[0]
        if tag != 0xC1832A9E:
            raise ValueError("bad Unreal package tag")
        legacy = struct.unpack_from("<i", data, 4)[0]
        ue3 = struct.unpack_from("<i", data, 8)[0]
        ue4 = struct.unpack_from("<i", data, 12)[0]
        ue5 = struct.unpack_from("<i", data, 16)[0]
        licensee = struct.unpack_from("<i", data, 20)[0]
        cursor = 24
        custom_count = struct.unpack_from("<i", data, cursor)[0]
        cursor += 4
        custom_versions = []
        for _ in range(max(0, min(custom_count, 64))):
            if cursor + 20 > len(data):
                break
            guid = data[cursor : cursor + 16].hex()
            version = struct.unpack_from("<i", data, cursor + 16)[0]
            custom_versions.append({"guid": guid, "version": version})
            cursor += 20
        total_header = struct.unpack_from("<i", data, cursor)[0]
        cursor += 4
        name_length = struct.unpack_from("<i", data, cursor)[0]
        cursor += 4
        if 0 < name_length < 512 and cursor + name_length <= len(data):
            package_name = data[cursor : cursor + name_length].rstrip(b"\0").decode("utf-8", errors="replace")
        else:
            package_name = None
        return {
            "legacy_file_version": legacy,
            "ue3_version": ue3,
            "ue4_version": ue4,
            "ue5_version": ue5,
            "licensee_version": licensee,
            "custom_versions": custom_versions,
            "total_header_size": total_header,
            "package_name": package_name,
        }


class UnrealPakAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="unreal_pak",
        name="Unreal pak footer",
        formats=("ue_pak",),
        inputs=("file",),
        output_schema="unreal_pak.v1",
        dependencies=("fileformat.ue_pak",),
        limitations=(
            "Footer scan only; the index is not parsed.",
            "Encrypted indexes are reported but not decrypted.",
        ),
        verification="partially_verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "ue_pak"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 4096))
            tail = handle.read(4096)
        magic = b"\xe1\x12\x6f\x5a"
        index = tail.rfind(magic)
        if index < 0:
            raise ValueError("pak footer magic not found")
        version = struct.unpack_from("<I", tail, index + 4)[0] if index + 8 <= len(tail) else None
        index_offset = struct.unpack_from("<q", tail, index + 8)[0] if index + 16 <= len(tail) else None
        index_size = struct.unpack_from("<q", tail, index + 16)[0] if index + 24 <= len(tail) else None
        return {
            "file_size": size,
            "footer_offset": size - len(tail) + index,
            "version": version,
            "index_offset": index_offset,
            "index_size": index_size,
            "footer_plausible": bool(
                version is not None
                and 0 < version < 32
                and index_offset is not None
                and 0 <= index_offset <= size
            ),
        }


class GodotPckAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="godot_pck",
        name="Godot PCK header",
        formats=("godot_pck",),
        inputs=("file",),
        output_schema="godot_pck.v1",
        dependencies=("fileformat.godot_pck",),
        limitations=(
            "Header only; file entries are not enumerated.",
            "Encrypted packs are out of scope.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "godot_pck"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        data = context.path.read_bytes()[: 256]
        if data[:4] != b"GDPC":
            raise ValueError("bad Godot PCK magic")
        pack_version = struct.unpack_from("<I", data, 4)[0]
        major, minor, patch = struct.unpack_from("<III", data, 8)
        return {
            "pack_format_version": pack_version,
            "engine_version": f"{major}.{minor}.{patch}",
            "pack_format_hint": "godot4" if pack_version >= 2 else "godot3",
        }


class GameMakerAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="gamemaker_data",
        name="GameMaker data.win chunk table",
        formats=("gamemaker_datawin",),
        inputs=("file",),
        output_schema="gamemaker_data.v1",
        dependencies=("fileformat.gamemaker_datawin",),
        limitations=(
            "Chunk table only; bytecode and assets are not decoded.",
            "Format varies across GameMaker runtime versions.",
        ),
        verification="partially_verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "gamemaker_datawin"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        path = context.path
        size = path.stat().st_size
        chunks = []
        with path.open("rb") as handle:
            header = handle.read(12)
            if header[:4] != b"FORM" or header[8:12] != b"GEN8":
                raise ValueError("not a GameMaker GEN8 file")
            cursor = 12
            while cursor + 8 <= size and len(chunks) < 256:
                handle.seek(cursor)
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                name = chunk[:4].decode("ascii", errors="replace")
                chunk_size = struct.unpack_from("<I", chunk, 4)[0]
                if not name.isprintable() or chunk_size == 0 or cursor + 8 + chunk_size > size:
                    break
                chunks.append({"name": name, "offset": cursor + 8, "size": chunk_size})
                cursor += 8 + chunk_size
        return {"file_size": size, "chunks": chunks, "chunk_count": len(chunks)}


class RenpyAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="renpy_archive",
        name="Ren'Py RPA header",
        formats=("renpy_rpa",),
        inputs=("file",),
        output_schema="renpy_rpa.v1",
        dependencies=("fileformat.renpy_rpa",),
        limitations=(
            "Header only; the pickled index is not loaded.",
            "Encrypted archives are out of scope.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "renpy_rpa"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        header = context.path.read_bytes()[: 128]
        line = header.split(b"\n", 1)[0].decode("ascii", errors="replace")
        parts = line.split()
        version = parts[0].replace("RPA-", "") if parts else "?"
        offset = key = None
        try:
            offset = int(parts[1], 16)
            key = int(parts[2], 16)
        except (IndexError, ValueError):
            pass
        return {
            "archive_version": version,
            "index_offset": offset,
            "xor_key": f"0x{key:08x}" if key is not None else None,
            "header_plausible": offset is not None and 0 < offset < context.path.stat().st_size,
        }


class LoveAnalyzer(Analyzer):
    spec = AnalyzerSpec(
        id="love_archive",
        name="LÖVE .love archive",
        formats=("love_archive",),
        inputs=("file",),
        output_schema="love_archive.v1",
        dependencies=("fileformat.love_archive",),
        limitations=(
            "Lists entries and reads main.lua/conf.lua only.",
            "Compiled Lua bytecode is not decompiled.",
        ),
        verification="verified",
    )

    def detect(self, context: AnalysisContext) -> bool:
        return context.sniffed.format_id == "love_archive"

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        with zipfile.ZipFile(context.path) as archive:
            names = archive.namelist()
            main_lua = ""
            conf_lua = ""
            if "main.lua" in names:
                main_lua = archive.read("main.lua")[:4096].decode("utf-8", errors="replace")
            if "conf.lua" in names:
                conf_lua = archive.read("conf.lua")[:4096].decode("utf-8", errors="replace")
            return {
                "entries": len(names),
                "has_main_lua": "main.lua" in names,
                "has_conf_lua": "conf.lua" in names,
                "main_lua_preview": main_lua,
                "conf_lua_preview": conf_lua,
                "asset_extensions": sorted({name.rsplit(".", 1)[-1].lower() for name in names if "." in name})[:32],
            }


for analyzer in (
    UnityBundleAnalyzer(),
    UnityMetadataAnalyzer(),
    UnrealPackageAnalyzer(),
    UnrealPakAnalyzer(),
    GodotPckAnalyzer(),
    GameMakerAnalyzer(),
    RenpyAnalyzer(),
    LoveAnalyzer(),
):
    register(analyzer)
