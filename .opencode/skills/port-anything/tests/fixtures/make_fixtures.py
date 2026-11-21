"""Generate binary fixtures for analyzer tests.

Everything here is synthetic but structurally valid for the fields the
analyzers read. Fixtures are written to a caller-supplied directory and are
never committed as binaries.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path


# --------------------------------------------------------------------------- #
# PE
# --------------------------------------------------------------------------- #


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def make_pe(
    path: Path,
    *,
    machine: int = 0x8664,
    subsystem: int = 2,
    dll_name: str = "UnityPlayer.dll",
    import_name: str = "UnityMain",
    is_dll: bool = False,
) -> Path:
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)

    rdata = bytearray(0x400)
    descriptor = struct.pack("<IIIII", 0x2040, 0, 0, 0x2080, 0x2060)
    rdata[0:20] = descriptor
    struct.pack_into("<Q", rdata, 0x40, 0x2090)
    struct.pack_into("<Q", rdata, 0x60, 0x2090)
    name_bytes = dll_name.encode("ascii") + b"\0"
    rdata[0x80 : 0x80 + len(name_bytes)] = name_bytes
    hint = struct.pack("<H", 0) + import_name.encode("ascii") + b"\0"
    rdata[0x90 : 0x90 + len(hint)] = hint

    text = b"\xc3" + b"\x00" * 0x1FF

    coff = struct.pack(
        "<HHIIIHH",
        machine,
        2,
        0,
        0,
        0,
        0xF0,
        0x0022 if not is_dll else 0x2022,
    )
    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B,
        14,
        0,
        0x200,
        0x600,
        0,
        0x1000,
        0x1000,
        0x140000000,
        0x1000,
        0x200,
        6,
        0,
        0,
        0,
        6,
        0,
        0,
        0x3000,
        0x200,
        0,
        subsystem,
        0x8160,
        0x100000,
        0x1000,
        0x100000,
        0x1000,
        0,
        16,
    )
    directories = bytearray(16 * 8)
    struct.pack_into("<II", directories, 8, 0x2000, 0x28)  # import directory
    sections = struct.pack(
        "<8sIIIIIIHHI",
        b".text",
        0x200,
        0x1000,
        0x200,
        0x200,
        0,
        0,
        0,
        0,
        0x60000020,
    ) + struct.pack(
        "<8sIIIIIIHHI",
        b".rdata",
        0x400,
        0x2000,
        0x400,
        0x400,
        0,
        0,
        0,
        0,
        0x40000040,
    )

    headers = bytes(dos) + b"PE\0\0" + coff + optional + bytes(directories) + sections
    headers = headers.ljust(0x200, b"\0")
    path.write_bytes(headers + text + bytes(rdata))
    return path


# --------------------------------------------------------------------------- #
# Mach-O
# --------------------------------------------------------------------------- #


def make_macho(path: Path, *, arm64: bool = True, platform: int = 1, dylib: str = "UnityPlayer.dylib") -> Path:
    cputype = 0x0100000C if arm64 else 0x01000007
    name = dylib.encode("utf-8") + b"\0"
    dylib_cmdsize = _align(24 + len(name), 8)
    build_cmdsize = 24
    ncmds = 2
    sizeofcmds = dylib_cmdsize + build_cmdsize

    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cputype, 3, 2, ncmds, sizeofcmds, 0, 0)

    dylib_cmd = struct.pack("<IIIIII", 0xC, dylib_cmdsize, 24, 0, 0x10000, 0x10000)
    dylib_cmd += name.ljust(dylib_cmdsize - 24, b"\0")

    build_cmd = struct.pack("<IIIIII", 0x32, build_cmdsize, platform, 0x000D0000, 0x000D0000, 0)

    path.write_bytes(header + build_cmd + dylib_cmd)
    return path


# --------------------------------------------------------------------------- #
# AXML + APK
# --------------------------------------------------------------------------- #

TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_BOOLEAN = 0x12
NO_INDEX = 0xFFFFFFFF


def _string_pool(strings: list[str]) -> bytes:
    encoded = []
    for text in strings:
        raw = text.encode("utf-16-le")
        encoded.append(struct.pack("<H", len(text)) + raw + b"\0\0")
    data = b"".join(encoded)
    offsets = []
    cursor = 0
    for chunk in encoded:
        offsets.append(cursor)
        cursor += len(chunk)
    strings_start = 28 + 4 * len(strings)
    body = struct.pack("<" + "I" * len(offsets), *offsets) + data
    size = strings_start + len(data)
    padding = _align(size, 4) - size
    header = struct.pack(
        "<HHIIIIII",
        0x0001,
        28,
        size + padding,
        len(strings),
        0,
        0,
        strings_start,
        0,
    )
    return header + body + b"\0" * padding


def _start_element(name_index: int, attributes: list[tuple[int, int, int, int]]) -> bytes:
    """attributes: (name_index, type, raw_index, data)."""
    body = struct.pack("<II", 0, NO_INDEX)  # lineNumber, comment
    body += struct.pack("<II", 0xFFFFFFFF, name_index)  # namespace, name
    body += struct.pack("<HHHHHH", 20, 20, len(attributes), 0, 0, 0)
    for attr_name, value_type, raw, data in attributes:
        body += struct.pack("<III", 0xFFFFFFFF, attr_name, raw)
        body += struct.pack("<HBBI", 8, 0, value_type, data)
    size = 8 + len(body)
    return struct.pack("<HHI", 0x0102, 16, size) + body


def _end_element(name_index: int) -> bytes:
    body = struct.pack("<II", 0, NO_INDEX) + struct.pack("<II", 0xFFFFFFFF, name_index)
    size = 8 + len(body)
    return struct.pack("<HHI", 0x0103, 16, size) + body


def make_manifest_axml(package: str = "com.example.porttest") -> bytes:
    app_name = package + ".App"
    activity_name = package + ".MainActivity"
    strings = [
        "manifest",
        "package",
        "versionCode",
        "versionName",
        "compileSdkVersion",
        "uses-sdk",
        "minSdkVersion",
        "targetSdkVersion",
        "uses-permission",
        "name",
        "application",
        "debuggable",
        "extractNativeLibs",
        "activity",
        "android.permission.INTERNET",
        package,
        app_name,
        activity_name,
        "1.0",
    ]
    index = {text: position for position, text in enumerate(strings)}

    def sid(text: str) -> int:
        return index[text]

    def string_attr(name: str, value: str) -> tuple[int, int, int, int]:
        return (sid(name), TYPE_STRING, sid(value), sid(value))

    def int_attr(name: str, value: int) -> tuple[int, int, int, int]:
        return (sid(name), TYPE_INT_DEC, NO_INDEX, value)

    def bool_attr(name: str, value: bool) -> tuple[int, int, int, int]:
        return (sid(name), TYPE_INT_BOOLEAN, NO_INDEX, 1 if value else 0)

    chunks = [
        _start_element(
            sid("manifest"),
            [
                string_attr("package", package),
                int_attr("versionCode", 7),
                string_attr("versionName", "1.0"),
                int_attr("compileSdkVersion", 34),
            ],
        ),
        _start_element(sid("uses-sdk"), [int_attr("minSdkVersion", 24), int_attr("targetSdkVersion", 34)]),
        _end_element(sid("uses-sdk")),
        _start_element(sid("uses-permission"), [string_attr("name", "android.permission.INTERNET")]),
        _end_element(sid("uses-permission")),
        _start_element(
            sid("application"),
            [string_attr("name", app_name), bool_attr("debuggable", False), bool_attr("extractNativeLibs", True)],
        ),
        _start_element(sid("activity"), [string_attr("name", activity_name)]),
        _end_element(sid("activity")),
        _end_element(sid("application")),
        _end_element(sid("manifest")),
    ]
    pool = _string_pool(strings)
    body = pool + b"".join(chunks)
    total = 8 + len(body)
    return struct.pack("<HHI", 0x0003, 8, total) + body


def make_apk(
    path: Path,
    *,
    abis: tuple[str, ...] = ("arm64-v8a", "armeabi-v7a"),
    engine_lib: str = "libunity.so",
    with_assets: bool = True,
) -> Path:
    manifest = make_manifest_axml()
    dex = b"dex\n035\0" + b"\0" * 0x100
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", manifest)
        archive.writestr("classes.dex", dex)
        archive.writestr("META-INF/CERT.RSA", b"\x30\x82\x00\x00")
        for abi in abis:
            archive.writestr(f"lib/{abi}/{engine_lib}", b"\x7fELF" + b"\0" * 0x40)
            archive.writestr(f"lib/{abi}/libmain.so", b"\x7fELF" + b"\0" * 0x40)
        if with_assets:
            archive.writestr("assets/bin/Data/level0", b"level")
            archive.writestr("assets/bin/Data/resources.assets", b"assets")
    return path


def make_aab(path: Path) -> Path:
    manifest = make_manifest_axml("com.example.porttest.aab")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BundleConfig.pb", b"\x00" * 16)
        archive.writestr("base/manifest/AndroidManifest.xml", manifest)
        archive.writestr("base/dex/classes.dex", b"dex\n035\0" + b"\0" * 64)
    return path


# --------------------------------------------------------------------------- #
# Game containers
# --------------------------------------------------------------------------- #


def make_unity_bundle(path: Path, *, version: int = 6, unity: str = "2019.4.0f1") -> Path:
    header = b"UnityFS\0"
    header += struct.pack(">I", version)
    header += unity.encode("ascii") + b"\0"
    header += unity.encode("ascii") + b"\0"
    header += struct.pack(">q", 0x200)
    header += struct.pack(">III", 0x40, 0x80, 0x02)  # lz4
    path.write_bytes(header + b"\0" * (0x200 - len(header)))
    return path


def make_godot_pck(path: Path, *, major: int = 4, minor: int = 2, patch: int = 1) -> Path:
    header = b"GDPC" + struct.pack("<IIII", 2, major, minor, patch)
    path.write_bytes(header + b"\0" * 0x100)
    return path


def make_gamemaker(path: Path) -> Path:
    chunk = b"STRG" + struct.pack("<I", 8) + b"test\0\0\0\0"
    body = b"GEN8" + chunk
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


def make_renpy(path: Path) -> Path:
    path.write_bytes(b"RPA-3.0 0000000000000020 00000000\n" + b"\0" * 0x40)
    return path


def make_love(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("main.lua", "function love.draw() end\n")
        archive.writestr("conf.lua", "function love.conf(t) t.window.title = 'test' end\n")
        archive.writestr("assets/player.png", b"\x89PNG\r\n\x1a\n")
    return path


def make_unreal_pak(path: Path) -> Path:
    data = bytearray(0x400)
    footer = struct.pack("<I", 11) + struct.pack("<q", 0) + struct.pack("<q", 0x100)
    magic = b"\xe1\x12\x6f\x5a"
    data[-64 : -64 + 4] = magic
    data[-60 : -60 + len(footer)] = footer
    path.write_bytes(bytes(data))
    return path


def make_unreal_pak(path: Path) -> Path:
    data = bytearray(0x400)
    footer = struct.pack("<I", 11) + struct.pack("<q", 0) + struct.pack("<q", 0x100)
    magic = b"\xe1\x12\x6f\x5a"
    data[-64 : -64 + 4] = magic
    data[-60 : -60 + len(footer)] = footer
    path.write_bytes(bytes(data))
    return path


def make_iso9660(path: Path) -> Path:
    data = bytearray(0x9000)
    data[0x8001:0x8006] = b"CD001"
    data[0x8006] = 1
    path.write_bytes(bytes(data))
    return path


# --------------------------------------------------------------------------- #
# Source projects
# --------------------------------------------------------------------------- #


def make_cpp_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\n"
        "project(fixture CXX)\n"
        "find_package(SDL2 REQUIRED)\n"
        "add_executable(fixture src/main.cpp)\n",
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "readme.txt").write_text("asset\n", encoding="utf-8")
    return root


def make_godot_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        '[application]\nconfig/name="Fixture"\nconfig/features=PackedStringArray("4.2", "GL Compatibility")\n',
        encoding="utf-8",
    )
    (root / "main.gd").write_text("extends Node\n", encoding="utf-8")
    return root


def generate_all(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    return {
        "pe": make_pe(root / "fixture.exe"),
        "pe_arm64": make_pe(root / "fixture_arm64.exe", machine=0xAA64),
        "macho": make_macho(root / "fixture_macho"),
        "apk": make_apk(root / "fixture.apk"),
        "aab": make_aab(root / "fixture.aab"),
        "unity": make_unity_bundle(root / "fixture.bundle"),
        "godot_pck": make_godot_pck(root / "fixture.pck"),
        "gamemaker": make_gamemaker(root / "data.win"),
        "renpy": make_renpy(root / "archive.rpa"),
        "love": make_love(root / "fixture.love"),
        "unreal_pak": make_unreal_pak(root / "fixture.pak"),
        "iso": make_iso9660(root / "fixture.iso"),
        "cpp_project": make_cpp_project(root / "cpp_project"),
        "godot_project": make_godot_project(root / "godot_project"),
    }


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/generated")
    for name, path in generate_all(target).items():
        print(f"{name:<14} {path}")
