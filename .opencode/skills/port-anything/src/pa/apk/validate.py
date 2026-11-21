"""Structural validation of a packaged APK.

Reads the archive without extracting it and checks the properties that decide
whether a device will even accept the file: manifest present and parseable, dex
magic valid, native libraries in ABI directories, no duplicate entries, and
stored entries aligned to four bytes (which ``zipalign`` would enforce).
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any

from ..re.axml import AxmlError, parse_axml

DEX_MAGIC = b"dex\n"
VALID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64", "riscv64")


def _data_offset(info: zipfile.ZipInfo) -> int:
    return info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)


def inspect(apk: str | Path) -> dict[str, Any]:
    path = Path(apk)
    findings: list[dict[str, str]] = []
    if not path.is_file():
        return {
            "ok": False,
            "path": str(path),
            "findings": [{"severity": "error", "message": "file does not exist"}],
        }
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return {
            "ok": False,
            "path": str(path),
            "findings": [{"severity": "error", "message": f"not a readable zip: {exc}"}],
        }

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            findings.append(
                {"severity": "error", "message": f"duplicate zip entries: {', '.join(duplicates[:5])}"}
            )

        package_name = None
        manifest_size = 0
        if "AndroidManifest.xml" not in names:
            findings.append({"severity": "error", "message": "AndroidManifest.xml is missing"})
        else:
            manifest_size = archive.getinfo("AndroidManifest.xml").file_size
            try:
                document = parse_axml(archive.read("AndroidManifest.xml"))
                for element in document.get("elements", []):
                    if element.get("name") == "manifest":
                        package_name = element.get("attributes", {}).get("package")
                        break
                if package_name is None:
                    findings.append(
                        {"severity": "warning", "message": "manifest parsed but package attribute not found"}
                    )
            except (AxmlError, KeyError, struct.error) as exc:
                findings.append({"severity": "error", "message": f"manifest is not valid binary XML: {exc}"})

        dex_entries = sorted(name for name in names if name.startswith("classes") and name.endswith(".dex"))
        dex_valid = True
        for name in dex_entries:
            head = archive.read(name)[:8]
            if not head.startswith(DEX_MAGIC):
                dex_valid = False
                findings.append({"severity": "error", "message": f"{name} does not start with dex magic"})

        abi_dirs = sorted(
            {
                parts[1]
                for name in names
                if name.startswith("lib/") and len(parts := name.split("/")) >= 3 and parts[2]
            }
        )
        unknown_abis = [abi for abi in abi_dirs if abi not in VALID_ABIS]
        if unknown_abis:
            findings.append(
                {"severity": "warning", "message": f"unrecognised ABI directories: {', '.join(unknown_abis)}"}
            )
        native_count = sum(1 for name in names if name.startswith("lib/") and name.endswith(".so"))

        unaligned = [
            info.filename
            for info in infos
            if info.compress_type == zipfile.ZIP_STORED and _data_offset(info) % 4 != 0
        ]
        if unaligned:
            findings.append(
                {
                    "severity": "warning",
                    "message": f"{len(unaligned)} stored entr{'y is' if len(unaligned) == 1 else 'ies are'} not 4-byte aligned (zipalign would fix this)",
                }
            )

    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "ok": not errors,
        "path": str(path),
        "bytes": path.stat().st_size,
        "entries": len(names),
        "package": package_name,
        "manifest_bytes": manifest_size,
        "dex": dex_entries,
        "dex_magic_ok": dex_valid,
        "abis": abi_dirs,
        "native_libraries": native_count,
        "aligned": not unaligned,
        "findings": findings,
    }


def check_package_name(apk: str | Path, expected: str) -> dict[str, Any]:
    report = inspect(apk)
    actual = report.get("package")
    return {
        "ok": bool(expected) and actual == expected,
        "expected": expected,
        "actual": actual,
        "detail": (
            "package name matches"
            if actual == expected
            else f"package name {actual!r} does not match expected {expected!r}"
        ),
    }


def render(report: dict[str, Any]) -> str:
    if "entries" not in report:
        lines = [f"apk      : {report.get('path')}"]
        lines.extend(f"error    : {item['message']}" for item in report.get("findings", []))
        return "\n".join(lines + ["FAILED"])
    lines = [
        f"apk      : {report['path']}",
        f"size     : {report['bytes']} bytes, {report['entries']} entries",
        f"package  : {report.get('package') or 'UNKNOWN'}",
        f"dex      : {', '.join(report['dex']) or 'none'} (magic {'ok' if report['dex_magic_ok'] else 'BAD'})",
        f"native   : {report['native_libraries']} librar{'y' if report['native_libraries'] == 1 else 'ies'}"
        f" in {', '.join(report['abis']) or 'no ABI directory'}",
        f"aligned  : {'yes' if report['aligned'] else 'no'}",
    ]
    for item in report["findings"]:
        lines.append(f"{item['severity']:<8} : {item['message']}")
    lines.append("OK" if report["ok"] else "FAILED")
    return "\n".join(lines)