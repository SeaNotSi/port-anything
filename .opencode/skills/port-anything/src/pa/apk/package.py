"""APK packaging through the aapt2 + d8 + apksigner path.

This is the fallback pipeline for hosts without Gradle or the Android SDK. It
does not stand in for an SDK build: resource linking needs the framework
``android.jar`` for attribute resolution, so on a host without it the resource
stage is reported ``unsupported`` rather than silently producing an APK that
cannot carry a real manifest.

The commands are the ones the tools document:

    aapt2 compile --dir res -o compiled.zip
    aapt2 link --manifest AndroidManifest.xml -I android.jar -o base.apk compiled.zip
    d8 --output out classes/**.class
    apksigner sign --ks debug.jks --out signed.apk base.apk
    apksigner verify signed.apk
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..util import proc
from . import env, stages
from .stages import Stage


@dataclass
class ProjectSpec:
    manifest: Path
    out: Path = Path("build/app-unsigned.apk")
    resources: Path | None = None
    classes: tuple[Path, ...] = ()
    native_libs: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    assets: Path | None = None
    framework_jar: str | None = None
    keystore: Path | None = None
    keystore_pass: str = ""
    key_pass: str = ""
    key_alias: str = ""
    package_name: str = ""
    version_code: int | None = None
    version_name: str = ""
    min_sdk: int = 24

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectSpec":
        if not isinstance(data, dict):
            raise ValueError("project spec must be a JSON object")
        if not data.get("manifest"):
            raise ValueError("project spec needs a manifest path")
        libs = data.get("native_libs") or {}
        if not isinstance(libs, dict):
            raise ValueError("native_libs must map abi -> [paths]")
        return cls(
            manifest=Path(str(data["manifest"])).expanduser(),
            out=Path(str(data.get("out") or "build/app-unsigned.apk")).expanduser(),
            resources=Path(str(data["resources"])).expanduser() if data.get("resources") else None,
            classes=tuple(Path(str(item)).expanduser() for item in (data.get("classes") or [])),
            native_libs={
                str(abi): tuple(Path(str(item)).expanduser() for item in paths or [])
                for abi, paths in libs.items()
            },
            assets=Path(str(data["assets"])).expanduser() if data.get("assets") else None,
            framework_jar=data.get("framework_jar"),
            keystore=Path(str(data["keystore"])).expanduser() if data.get("keystore") else None,
            keystore_pass=str(data.get("keystore_pass") or ""),
            key_pass=str(data.get("key_pass") or ""),
            key_alias=str(data.get("key_alias") or ""),
            package_name=str(data.get("package_name") or ""),
            version_code=int(data["version_code"]) if data.get("version_code") is not None else None,
            version_name=str(data.get("version_name") or ""),
            min_sdk=int(data.get("min_sdk") or 24),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ProjectSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def compile_resources(aapt2: str, resources: Path, out_zip: Path, *, timeout: int = 300) -> Stage:
    if not resources.is_dir():
        return stages.fail("resources", f"resource directory not found: {resources}")
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    result = proc.run(
        [aapt2, "compile", "--dir", str(resources), "-o", str(out_zip)],
        timeout=timeout,
    )
    if result.ok and out_zip.is_file():
        return stages.ok(
            "resources",
            f"compiled resources into {out_zip.name}",
            command=list(result.argv),
            output=result.summary(),
            artifacts=[str(out_zip)],
        )
    return stages.from_result("resources", result, detail="aapt2 compile failed")


def link(
    aapt2: str,
    manifest: Path,
    out_apk: Path,
    *,
    compiled: Path | None = None,
    framework_jar: str | None = None,
    assets: Path | None = None,
    package_name: str = "",
    version_code: int | None = None,
    version_name: str = "",
    timeout: int = 300,
) -> Stage:
    if not manifest.is_file():
        return stages.fail("link", f"manifest not found: {manifest}")
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    argv = [aapt2, "link", "--manifest", str(manifest), "-o", str(out_apk)]
    if package_name:
        argv += ["--rename-manifest-package", package_name]
    # -version-code and -version-name make aapt2 add android: attributes, which
    # need the framework to resolve. They are only passed when asked for.
    if version_code is not None:
        argv += ["--version-code", str(version_code)]
    if version_name:
        argv += ["--version-name", version_name]
    if framework_jar:
        argv += ["-I", str(framework_jar)]
    if assets and assets.is_dir():
        argv += ["-A", str(assets)]
    if compiled:
        argv.append(str(compiled))
    result = proc.run(argv, timeout=timeout)
    if result.ok and out_apk.is_file():
        return stages.ok(
            "link",
            f"linked {out_apk.name}",
            command=list(result.argv),
            output=result.summary(),
            artifacts=[str(out_apk)],
        )
    return stages.from_result(
        "link",
        result,
        detail="aapt2 link failed (a manifest with android: attributes needs framework android.jar)",
    )


def _class_files(entries: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for entry in entries:
        if entry.is_dir():
            files.extend(sorted(entry.rglob("*.class")))
        elif entry.is_file():
            files.append(entry)
    return files


def dex(d8: str, class_entries: Iterable[Path], out_dir: Path, *, timeout: int = 600) -> Stage:
    files = _class_files(class_entries)
    if not files:
        return stages.unsupported("dex", "no compiled classes were provided")
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = [d8, "--output", str(out_dir), *[str(item) for item in files]]
    result = proc.run(argv, timeout=timeout)
    produced = out_dir / "classes.dex"
    if result.ok and produced.is_file():
        return stages.ok(
            "dex",
            f"d8 produced classes.dex from {len(files)} class file(s)",
            command=list(result.argv),
            output=result.summary(),
            artifacts=[str(produced)],
        )
    return stages.from_result("dex", result, detail="d8 failed")


def embed(
    apk: Path,
    *,
    dex_dir: Path | None = None,
    native_libs: dict[str, tuple[Path, ...]] | None = None,
    assets: Path | None = None,
) -> Stage:
    """Add dex, native libraries and assets to a linked APK."""
    if not apk.is_file():
        return stages.fail("embed", f"base APK missing: {apk}")
    added: list[str] = []
    try:
        with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as archive:
            existing = set(archive.namelist())

            def put(source: Path, arcname: str) -> None:
                if arcname in existing:
                    return
                archive.write(source, arcname)
                existing.add(arcname)
                added.append(arcname)

            if dex_dir and dex_dir.is_dir():
                for dex_file in sorted(dex_dir.glob("classes*.dex")):
                    put(dex_file, dex_file.name)
            for abi, paths in (native_libs or {}).items():
                for library in paths:
                    if library.name.endswith(".so"):
                        put(library, f"lib/{abi}/{library.name}")
            if assets and assets.is_dir():
                for item in sorted(assets.rglob("*")):
                    if item.is_file():
                        put(item, "assets/" + item.relative_to(assets).as_posix())
    except (OSError, zipfile.BadZipFile) as exc:
        return stages.fail("embed", f"could not write APK entries: {exc}")
    if not added:
        return stages.unsupported("embed", "nothing to embed (no dex, native libraries or assets)")
    return stages.ok(
        "embed",
        f"embedded {len(added)} entr{'y' if len(added) == 1 else 'ies'}",
        artifacts=added,
    )


def sign(
    apksigner: str,
    apk: Path,
    out_apk: Path,
    *,
    keystore: Path,
    keystore_pass: str,
    key_alias: str = "",
    key_pass: str = "",
    min_sdk: int = 0,
    timeout: int = 300,
) -> Stage:
    if not apk.is_file():
        return stages.fail("sign", f"unsigned APK missing: {apk}")
    if not keystore.is_file():
        return stages.unsupported("sign", f"keystore not found: {keystore}")
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        apksigner,
        "sign",
        "--ks",
        str(keystore),
        "--ks-pass",
        f"pass:{keystore_pass}",
        "--out",
        str(out_apk),
    ]
    if min_sdk:
        # Without this apksigner cannot pick signature schemes when the manifest
        # carries no minSdkVersion (which happens when no framework jar was used).
        argv += ["--min-sdk-version", str(int(min_sdk))]
    if key_alias:
        argv += ["--ks-key-alias", key_alias]
    if key_pass:
        argv += ["--key-pass", f"pass:{key_pass}"]
    argv.append(str(apk))
    result = proc.run(argv, timeout=timeout)
    if result.ok and out_apk.is_file():
        return stages.ok(
            "sign",
            f"signed {out_apk.name}",
            command=list(result.argv),
            output=result.summary(),
            artifacts=[str(out_apk)],
        )
    # Never echo the keystore password back into a report.
    return stages.from_result("sign", result, detail="apksigner sign failed")


def verify(apksigner: str, apk: Path, *, min_sdk: int = 0, timeout: int = 120) -> Stage:
    if not apk.is_file():
        return stages.fail("verify", f"APK missing: {apk}")
    argv = [apksigner, "verify", "--verbose", "--print-certs"]
    if min_sdk:
        # Same reason as sign(): with no minSdkVersion in the manifest apksigner
        # assumes API 1 and then rejects SHA-256 JAR signatures as unsupported.
        argv += ["--min-sdk-version", str(int(min_sdk))]
    argv.append(str(apk))
    result = proc.run(argv, timeout=timeout)
    return stages.from_result("verify", result, detail="apksigner verify failed" if not result.ok else "signature verified")


def selftest(workdir: str | Path | None = None) -> dict[str, Any]:
    """Build and sign a minimal APK to prove the packaging mechanics here.

    This deliberately does not pretend to be a port. It links a manifest with no
    ``android:`` attributes (which needs no framework jar), dexes one plain Java
    class, signs with a throwaway key and verifies the signature.
    """
    tools = {name: env.probe(name) for name in ("aapt2", "d8", "apksigner", "javac", "keytool")}
    missing = [name for name, tool in tools.items() if not tool.ok]
    temp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="pa-apk-selftest-"))
    temp.mkdir(parents=True, exist_ok=True)
    if missing:
        return {
            "ok": False,
            "workdir": str(temp),
            "missing": missing,
            "stages": [
                stages.unsupported("selftest", "missing tools: " + ", ".join(missing)).as_dict()
            ],
        }

    manifest = temp / "AndroidManifest.xml"
    manifest.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="dev.portanything.selftest"/>\n',
        encoding="utf-8",
    )
    source = temp / "Selftest.java"
    source.write_text(
        "public final class Selftest {\n"
        "    private Selftest() {}\n"
        "    public static int add(int a, int b) { return a + b; }\n"
        "}\n",
        encoding="utf-8",
    )
    classes = temp / "classes"
    classes.mkdir(exist_ok=True)
    results: list[Stage] = []

    javac = proc.run(
        [str(tools["javac"].path), "-source", "8", "-target", "8", "-d", str(classes), str(source)],
        timeout=300,
    )
    results.append(stages.from_result("javac", javac, detail="compiled the selftest class"))
    if not javac.ok:
        return {"ok": False, "workdir": str(temp), "missing": [], "stages": [s.as_dict() for s in results]}

    base_apk = temp / "base.apk"
    results.append(link(str(tools["aapt2"].path), manifest, base_apk))
    results.append(dex(str(tools["d8"].path), [classes], classes))
    results.append(embed(base_apk, dex_dir=classes))

    keystore = temp / "debug.p12"
    # Re-running in the same workdir must not fail on an existing alias.
    keystore.unlink(missing_ok=True)
    keytool = proc.run(
        [
            str(tools["keytool"].path),
            "-genkeypair",
            "-storetype",
            "PKCS12",
            "-keystore",
            str(keystore),
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-alias",
            "debug",
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "3650",
            "-dname",
            "CN=Port Anything Selftest",
        ],
        timeout=120,
    )
    results.append(stages.from_result("keystore", keytool, detail="generated a throwaway signing key"))

    signed = temp / "selftest-signed.apk"
    results.append(
        sign(
            str(tools["apksigner"].path),
            base_apk,
            signed,
            keystore=keystore,
            keystore_pass="android",
            key_alias="debug",
            key_pass="android",
            min_sdk=24,
        )
    )
    results.append(verify(str(tools["apksigner"].path), signed, min_sdk=24))

    return {
        "ok": all(stage.ok for stage in results),
        "workdir": str(temp),
        "missing": [],
        "artifact": str(signed) if signed.is_file() else None,
        "stages": [stage.as_dict() for stage in results],
    }


def clean(path: str | Path) -> None:
    """Remove a selftest work directory, and nothing else."""
    target = Path(path)
    if target.name.startswith("pa-apk-selftest-"):
        shutil.rmtree(target, ignore_errors=True)