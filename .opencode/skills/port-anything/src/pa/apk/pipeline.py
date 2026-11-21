"""Pipeline orchestration.

Runs the packaging stages in order, stops early when a prerequisite is missing,
and reports each stage with its own state. The claims block is the point of the
whole module: an APK existing says nothing about installation or runtime, and
``port_verified`` stays false until runtime evidence exists.

Stage states use the vocabulary from :mod:`pa.apk.stages`:
``done`` / ``failed`` / ``unsupported`` / ``skipped`` / ``pending``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import proc
from . import cpp, env, package, rust, smoke, stages, strategy, validate
from .stages import Stage

REQUIRED_TOOLS = ("aapt2", "d8", "apksigner")


@dataclass
class PipelineReport:
    strategy: str
    stages: list[Stage] = field(default_factory=list)
    artifact: str | None = None
    tools: dict[str, Any] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> list[Stage]:
        return [stage for stage in self.stages if stage.state == stages.FAILED]

    @property
    def unsupported(self) -> list[Stage]:
        return [stage for stage in self.stages if stage.state == stages.UNSUPPORTED]

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "ok": self.ok,
            "stages": [stage.as_dict() for stage in self.stages],
            "state_counts": stages.summarise(self.stages),
            "artifact": self.artifact,
            "tools": self.tools,
            "claims": self.claims,
        }

    def render(self) -> str:
        lines = [f"strategy : {self.strategy}"]
        for stage in self.stages:
            flag = stage.state.upper()
            lines.append(f"{flag:<12} {stage.id:<14} {stage.detail[:96]}")
        claims = self.claims
        lines.append("")
        lines.append(f"artifact : {self.artifact or 'none'}")
        lines.append(
            "claims   : artifact={} signed={} installed={} runtime={} port_verified={}".format(
                claims.get("artifact_exists"),
                claims.get("signed"),
                claims.get("installed"),
                claims.get("runtime_verified"),
                claims.get("port_verified"),
            )
        )
        lines.append(claims.get("statement", ""))
        lines.append("OK" if self.ok else "FAILED")
        return "\n".join(lines)


def _toolchain_stage(report: dict[str, Any]) -> Stage:
    tools = report["tools"]
    missing = [name for name in REQUIRED_TOOLS if tools[name]["state"] != "ok"]
    if missing:
        reasons = ", ".join(f"{name}: {tools[name]['state']}" for name in missing)
        return stages.unsupported("toolchain", f"packaging tools unavailable ({reasons})")
    versions = ", ".join(f"{name} {tools[name]['version']}" for name in REQUIRED_TOOLS)
    return stages.ok("toolchain", versions)


def run(
    spec: package.ProjectSpec,
    *,
    strategy_id: str = "native_port",
    check_device: bool = True,
    timeout: int = 900,
) -> PipelineReport:
    plan = strategy.plan(strategy_id)
    report = PipelineReport(strategy=plan.strategy)
    environment = env.detect()
    report.tools = environment
    framework = spec.framework_jar or environment["android"]["framework_jar"]

    toolchain = _toolchain_stage(environment)
    report.stages.append(toolchain)
    if not toolchain.ok:
        for stage_id in ("resources", "link", "dex", "native", "embed", "align", "sign", "verify"):
            report.stages.append(stages.unsupported(stage_id, "skipped: packaging toolchain unavailable"))
        _finish(report, spec)
        return report

    tools = {name: environment["tools"][name]["path"] for name in REQUIRED_TOOLS}

    workdir = spec.out.parent
    workdir.mkdir(parents=True, exist_ok=True)
    compiled = workdir / "compiled-resources.zip"

    if spec.resources:
        resource_stage = package.compile_resources(str(tools["aapt2"]), spec.resources, compiled, timeout=timeout)
        report.stages.append(resource_stage)
        if not resource_stage.ok:
            _finish(report, spec)
            return report
    else:
        report.stages.append(stages.skipped("resources", "no resource directory in the spec"))

    base = workdir / "base-unsigned.apk"
    detail = ""
    if spec.resources and not framework:
        detail = (
            "no framework android.jar found; a manifest with android: attributes cannot resolve "
            "attribute ids (set ANDROID_SDK_ROOT or framework_jar)"
        )
    link_stage = package.link(
        str(tools["aapt2"]),
        spec.manifest,
        base,
        compiled=compiled if spec.resources and compiled.is_file() else None,
        framework_jar=framework,
        assets=spec.assets,
        package_name=spec.package_name,
        version_code=spec.version_code,
        version_name=spec.version_name,
        timeout=timeout,
    )
    if detail and not link_stage.ok:
        link_stage.detail = detail
    report.stages.append(link_stage)
    if not link_stage.ok:
        _finish(report, spec)
        return report

    dex_dir = workdir / "dex"
    dex_stage = package.dex(str(tools["d8"]), spec.classes, dex_dir, timeout=timeout)
    report.stages.append(dex_stage)

    native_stage = _native_stage(spec)
    report.stages.append(native_stage)

    embed_stage = package.embed(
        base,
        dex_dir=dex_dir if dex_stage.ok else None,
        native_libs=spec.native_libs,
        assets=None,  # aapt2 already packed assets via -A
    )
    report.stages.append(embed_stage)

    zipalign = environment["tools"]["zipalign"]
    if zipalign["state"] == "ok":
        aligned = proc.run([str(zipalign["path"]), "-c", "-v", "4", str(base)], timeout=timeout)
        report.stages.append(stages.from_result("align", aligned, detail="zipalign check"))
    else:
        report.stages.append(
            stages.unsupported(
                "align",
                f"zipalign is {zipalign['state']}; the APK is not verified as 4-byte aligned",
            )
        )

    if spec.keystore and spec.keystore.is_file():
        signed = workdir / "app-signed.apk"
        sign_stage = package.sign(
            str(tools["apksigner"]),
            base,
            signed,
            keystore=spec.keystore,
            keystore_pass=spec.keystore_pass,
            key_alias=spec.key_alias,
            key_pass=spec.key_pass or spec.keystore_pass,
            min_sdk=spec.min_sdk,
            timeout=timeout,
        )
        report.stages.append(sign_stage)
        target = signed if sign_stage.ok else base
        report.stages.append(package.verify(str(tools["apksigner"]), target, min_sdk=spec.min_sdk, timeout=timeout))
        report.artifact = str(target) if target.is_file() else None
    else:
        report.stages.append(stages.unsupported("sign", "no keystore in the spec; the APK stays unsigned"))
        report.stages.append(stages.unsupported("verify", "nothing signed to verify"))
        report.artifact = str(base) if base.is_file() else None

    if report.artifact:
        inspection = validate.inspect(report.artifact)
        state = stages.DONE if inspection["ok"] else stages.FAILED
        report.stages.append(
            stages.Stage(
                "inspect",
                state,
                f"{inspection['entries']} entries, package {inspection.get('package') or 'unknown'}",
                artifacts=[report.artifact],
                output=validate.render(inspection),
            )
        )

    if check_device:
        for stage in (smoke.devices(), smoke.install(report.artifact) if report.artifact else stages.unsupported("install", "no artifact")):
            report.stages.append(stage)
        if report.artifact and spec.package_name:
            report.stages.append(smoke.launch(spec.package_name))
            report.stages.append(smoke.logcat(package=spec.package_name))
        else:
            report.stages.append(stages.unsupported("launch", "no package name in the spec"))
            report.stages.append(stages.unsupported("runtime", "no package name in the spec"))
    else:
        report.stages.append(stages.skipped("install", "device checks disabled"))
        report.stages.append(stages.skipped("launch", "device checks disabled"))
        report.stages.append(stages.skipped("runtime", "device checks disabled"))

    _finish(report, spec)
    return report


def _native_stage(spec: package.ProjectSpec) -> Stage:
    if not spec.native_libs:
        return stages.skipped("native", "no native libraries in the spec")
    missing: list[str] = []
    found: list[str] = []
    for abi, paths in spec.native_libs.items():
        for item in paths:
            if Path(item).is_file():
                found.append(f"{abi}:{Path(item).name}")
            else:
                missing.append(str(item))
    if missing:
        return stages.fail("native", f"declared libraries are missing: {', '.join(missing[:4])}")
    return stages.ok("native", f"{len(found)} librar{'y' if len(found) == 1 else 'ies'}: {', '.join(found[:6])}")


def _finish(report: PipelineReport, spec: package.ProjectSpec) -> None:
    by_id = {stage.id: stage for stage in report.stages}
    artifact_exists = bool(report.artifact and Path(report.artifact).is_file())
    signed = by_id.get("verify", Stage("verify", stages.PENDING)).state == stages.DONE
    installed = by_id.get("install", Stage("install", stages.PENDING)).state == stages.DONE
    runtime = by_id.get("runtime", Stage("runtime", stages.PENDING)).state == stages.DONE
    report.claims = {
        "artifact_exists": artifact_exists,
        "signed": signed,
        "installed": installed,
        "runtime_verified": runtime,
        "port_verified": False,
        "statement": (
            "packaged and signed artifacts are not a port; port_verified stays false until "
            "launch and gameplay evidence exist on a device"
        ),
        "package_name": spec.package_name or None,
    }


def build_from_spec_file(path: str | Path, *, strategy_id: str = "native_port", check_device: bool = True) -> PipelineReport:
    spec = package.ProjectSpec.from_json(path)
    return run(spec, strategy_id=strategy_id, check_device=check_device)


def build_native(
    project_dir: str | Path,
    *,
    abi: str = "arm64-v8a",
    kind: str = "rust",
    release: bool = True,
    offline: bool = False,
    build_dir: str | Path | None = None,
    api: int = 24,
) -> dict[str, Any]:
    """Build native code only, returning the stage(s) and the libraries found."""
    if kind == "rust":
        return rust.build(project_dir, abi=abi, release=release, offline=offline)
    if kind == "cpp":
        workdir = Path(build_dir) if build_dir else Path(project_dir) / "build" / abi
        return cpp.android_libraries(project_dir, workdir, abi=abi, api=api)
    raise ValueError(f"unknown native kind {kind!r}; expected 'rust' or 'cpp'")