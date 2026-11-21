"""Unit and integration tests for the APK build module.

The shelling-out code is tested with a fake ``proc.run`` so the assertions are
about *decisions* (which command is built, which stage state results) rather
than about a tool being installed. Two tests touch the real host: one inspects a
synthetic APK built from the fixture manifest, the other runs the full selftest
only when aapt2/d8/apksigner/javac/keytool are all present.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))
sys.path.insert(0, str(SKILL_ROOT / "tests" / "fixtures"))

from make_fixtures import generate_all, make_manifest_axml  # noqa: E402

from pa.apk import cpp, env, package, pipeline, rust, smoke, stages, strategy, validate  # noqa: E402
from pa.apk.stages import Stage  # noqa: E402
from pa.util import proc as proc_module  # noqa: E402
from pa.util.proc import Result  # noqa: E402


class FakeProc:
    """A stand-in for ``proc.run`` that matches on argv tokens."""

    def __init__(self) -> None:
        self.rules: list[tuple[str, int, str, str, object]] = []
        self.calls: list[list[str]] = []

    def rule(self, token: str, *, returncode: int = 0, stdout: str = "", stderr: str = "", effect=None) -> "FakeProc":
        self.rules.append((token, returncode, stdout, stderr, effect))
        return self

    def __call__(self, argv, **kwargs) -> Result:
        self.calls.append([str(item) for item in argv])
        joined = " ".join(str(item) for item in argv)
        for token, returncode, stdout, stderr, effect in self.rules:
            if token in joined:
                if effect is not None:
                    effect([str(item) for item in argv])
                return Result(tuple(str(item) for item in argv), returncode, stdout, stderr)
        return Result(tuple(str(item) for item in argv), 0, "", "")

    def last(self, token: str) -> list[str]:
        matches = [call for call in self.calls if token in " ".join(call)]
        return matches[-1] if matches else []


def _value_after(argv: list[str], flag: str) -> str | None:
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def write_apk(path: Path, *, package_name: str = "com.example.porttest", dex_magic: bytes = b"dex\n035\0", with_native: bool = True) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", make_manifest_axml(package_name))
        archive.writestr("classes.dex", dex_magic + b"\0" * 0x40)
        if with_native:
            archive.writestr("lib/arm64-v8a/libmain.so", b"\x7fELF" + b"\0" * 0x20)
    return path


class StageTests(unittest.TestCase):
    def test_constructors_carry_state(self) -> None:
        self.assertEqual(stages.ok("a", "fine").state, stages.DONE)
        self.assertEqual(stages.fail("a", "bad").state, stages.FAILED)
        self.assertEqual(stages.unsupported("a", "no tool").state, stages.UNSUPPORTED)
        self.assertEqual(stages.skipped("a").state, stages.SKIPPED)
        self.assertEqual(stages.pending("a").state, stages.PENDING)

    def test_ok_state_is_the_only_ok(self) -> None:
        for state in (stages.FAILED, stages.UNSUPPORTED, stages.SKIPPED, stages.PENDING):
            self.assertFalse(Stage("x", state).ok)
        self.assertTrue(Stage("x", stages.DONE).ok)

    def test_unknown_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Stage("x", "probably")

    def test_from_result_flags_timeout_and_keeps_output(self) -> None:
        timed = Result(("tool",), 0, "", "", timed_out=True)
        stage = stages.from_result("run", timed)
        self.assertEqual(stage.state, stages.FAILED)
        failed = Result(("tool",), 2, "out", "err")
        stage = stages.from_result("run", failed, detail="custom")
        self.assertEqual(stage.state, stages.FAILED)
        self.assertIn("err", stage.output)
        self.assertEqual(stage.returncode, 2)

    def test_from_result_truncates_long_output(self) -> None:
        long = Result(("tool",), 0, "x" * 9000, "")
        stage = stages.from_result("run", long)
        self.assertLess(len(stage.output), 4100)

    def test_summarise_counts_every_state(self) -> None:
        counts = stages.summarise([stages.ok("a", ""), stages.fail("b", ""), stages.skipped("c")])
        self.assertEqual(counts[stages.DONE], 1)
        self.assertEqual(counts[stages.FAILED], 1)
        self.assertEqual(counts[stages.SKIPPED], 1)
        self.assertEqual(counts[stages.PENDING], 0)


class StrategyTests(unittest.TestCase):
    def test_every_strategy_has_a_plan(self) -> None:
        from pa.re.compat import STRATEGY_IDS

        for strategy_id in STRATEGY_IDS:
            plan = strategy.plan(strategy_id)
            self.assertEqual(plan.strategy, strategy_id)
            self.assertTrue(plan.steps)
            # Every plan must end in the device-evidence tail.
            for step in ("install", "launch", "runtime"):
                self.assertIn(step, plan.steps)

    def test_unknown_strategy_raises(self) -> None:
        with self.assertRaises(ValueError):
            strategy.plan("teleport")

    def test_from_compat_uses_recommended(self) -> None:
        plan = strategy.from_compat({"recommended": {"id": "emulator"}})
        self.assertEqual(plan.strategy, "emulator")

    def test_from_compat_defaults_without_recommendation(self) -> None:
        self.assertEqual(strategy.from_compat({}).strategy, "already_android")

    def test_render_lists_steps(self) -> None:
        text = strategy.render(strategy.plan("native_port"))
        self.assertIn("native_port", text)
        self.assertIn("1.", text)
        self.assertIn("rebuild required", text)

    def test_native_port_needs_source(self) -> None:
        self.assertTrue(strategy.plan("native_port").needs_source)
        self.assertFalse(strategy.plan("emulator").needs_source)


class EnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._which = proc_module.which
        self._run = proc_module.run

    def tearDown(self) -> None:
        proc_module.which = self._which
        proc_module.run = self._run

    def test_probe_missing(self) -> None:
        proc_module.which = lambda name: None
        tool = env.probe("aapt2")
        self.assertEqual(tool.state, "missing")
        self.assertFalse(tool.ok)

    def test_probe_reports_broken_not_present(self) -> None:
        proc_module.which = lambda name: f"/fake/{name}"

        def fake_run(argv, **kwargs):
            return Result(tuple(argv), 127, "", 'CANNOT LINK EXECUTABLE "/fake/adb"')

        proc_module.run = fake_run
        tool = env.probe("adb")
        self.assertEqual(tool.state, "broken")
        self.assertIn("CANNOT LINK", tool.error)

    def test_probe_ok_reads_first_line(self) -> None:
        proc_module.which = lambda name: f"/fake/{name}"
        proc_module.run = lambda argv, **kwargs: Result(tuple(argv), 0, "1.2.3\nmore\n", "")
        tool = env.probe("cmake")
        self.assertEqual(tool.state, "ok")
        self.assertEqual(tool.version, "1.2.3")

    def test_find_sdk_from_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pa-sdk-") as tmp:
            jar = Path(tmp) / "platforms" / "android-34" / "android.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"PK\x03\x04")
            previous = {key: os.environ.get(key) for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_SDK")}
            os.environ["ANDROID_SDK_ROOT"] = tmp
            for key in ("ANDROID_HOME", "ANDROID_SDK"):
                os.environ.pop(key, None)
            try:
                found = env.find_sdk()
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(found["root"], tmp)
            self.assertEqual(found["framework_jar"], str(jar))
            self.assertEqual(found["api"], 34)

    def test_find_ndk_missing(self) -> None:
        previous = {key: os.environ.get(key) for key in ("ANDROID_NDK_ROOT", "ANDROID_NDK_HOME", "NDK_ROOT", "NDK_HOME", "ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_SDK")}
        for key in previous:
            os.environ.pop(key, None)
        try:
            # A directory that definitely has no android.toolchain.cmake.
            found = env.find_ndk()
        finally:
            for key, value in previous.items():
                if value is not None:
                    os.environ[key] = value
        self.assertIn("root", found)
        self.assertIn("toolchain_file", found)


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-apk-unit-"))
        self.fake = FakeProc()
        self._run = proc_module.run
        proc_module.run = self.fake
        self.manifest = self.tmp / "AndroidManifest.xml"
        self.manifest.write_text("<manifest/>", encoding="utf-8")

    def tearDown(self) -> None:
        proc_module.run = self._run
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------------------------------------------------- project spec

    def test_spec_requires_manifest(self) -> None:
        with self.assertRaises(ValueError):
            package.ProjectSpec.from_dict({"out": "x.apk"})

    def test_spec_rejects_bad_native_libs(self) -> None:
        with self.assertRaises(ValueError):
            package.ProjectSpec.from_dict({"manifest": "m", "native_libs": ["a"]})

    def test_spec_from_json_round_trip(self) -> None:
        spec_file = self.tmp / "spec.json"
        spec_file.write_text(
            '{"manifest": "AndroidManifest.xml", "package_name": "dev.x", "min_sdk": 26, "native_libs": {"arm64-v8a": ["lib.so"]}}',
            encoding="utf-8",
        )
        spec = package.ProjectSpec.from_json(spec_file)
        self.assertEqual(spec.package_name, "dev.x")
        self.assertEqual(spec.min_sdk, 26)
        self.assertEqual(spec.native_libs["arm64-v8a"], (Path("lib.so"),))

    # ----------------------------------------------------------- compile

    def test_compile_missing_dir_fails(self) -> None:
        stage = package.compile_resources("/fake/aapt2", self.tmp / "nope", self.tmp / "c.zip")
        self.assertEqual(stage.state, stages.FAILED)

    def test_compile_requires_output_file(self) -> None:
        resources = self.tmp / "res"
        resources.mkdir()
        out = self.tmp / "c.zip"
        self.fake.rule("aapt2", effect=lambda argv: out.write_bytes(b"PK"))
        self.assertEqual(package.compile_resources("/fake/aapt2", resources, out).state, stages.DONE)

    def test_compile_reports_failure(self) -> None:
        resources = self.tmp / "res"
        resources.mkdir()
        self.fake.rule("aapt2", returncode=1, stderr="bad xml")
        stage = package.compile_resources("/fake/aapt2", resources, self.tmp / "c.zip")
        self.assertEqual(stage.state, stages.FAILED)
        self.assertIn("bad xml", stage.output)

    # ----------------------------------------------------------- link

    def test_link_missing_manifest_fails(self) -> None:
        stage = package.link("/fake/aapt2", self.tmp / "nope.xml", self.tmp / "base.apk")
        self.assertEqual(stage.state, stages.FAILED)

    def test_link_omits_version_flags_by_default(self) -> None:
        out = self.tmp / "base.apk"
        self.fake.rule("aapt2", effect=lambda argv: out.write_bytes(b"PK"))
        stage = package.link("/fake/aapt2", self.manifest, out)
        self.assertEqual(stage.state, stages.DONE)
        argv = self.fake.last("aapt2")
        self.assertNotIn("--version-code", argv)
        self.assertNotIn("--version-name", argv)

    def test_link_passes_version_flags_when_given(self) -> None:
        out = self.tmp / "base.apk"
        self.fake.rule("aapt2", effect=lambda argv: out.write_bytes(b"PK"))
        package.link("/fake/aapt2", self.manifest, out, version_code=7, version_name="1.0")
        argv = self.fake.last("aapt2")
        self.assertEqual(_value_after(argv, "--version-code"), "7")
        self.assertEqual(_value_after(argv, "--version-name"), "1.0")

    def test_link_passes_framework_and_assets(self) -> None:
        out = self.tmp / "base.apk"
        assets = self.tmp / "assets"
        assets.mkdir()
        self.fake.rule("aapt2", effect=lambda argv: out.write_bytes(b"PK"))
        package.link("/fake/aapt2", self.manifest, out, framework_jar="/sdk/android.jar", assets=assets)
        argv = self.fake.last("aapt2")
        self.assertEqual(_value_after(argv, "-I"), "/sdk/android.jar")
        self.assertEqual(_value_after(argv, "-A"), str(assets))

    # ----------------------------------------------------------- dex

    def test_dex_without_classes_is_unsupported(self) -> None:
        stage = package.dex("/fake/d8", [], self.tmp / "dex")
        self.assertEqual(stage.state, stages.UNSUPPORTED)

    def test_dex_produces_classes_dex(self) -> None:
        class_file = self.tmp / "A.class"
        class_file.write_bytes(b"\xca\xfe\xba\xbe")
        out_dir = self.tmp / "dex"

        def effect(argv):
            directory = Path(_value_after(argv, "--output"))
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "classes.dex").write_bytes(b"dex\n035\0")

        self.fake.rule("d8", effect=effect)
        stage = package.dex("/fake/d8", [class_file], out_dir)
        self.assertEqual(stage.state, stages.DONE)

    # ----------------------------------------------------------- embed

    def test_embed_missing_apk_fails(self) -> None:
        self.assertEqual(package.embed(self.tmp / "nope.apk").state, stages.FAILED)

    def test_embed_adds_dex_and_native_libraries(self) -> None:
        apk = write_apk(self.tmp / "base.apk", with_native=False)
        dex_dir = self.tmp / "dex"
        dex_dir.mkdir()
        (dex_dir / "classes.dex").write_bytes(b"dex\n035\0")
        lib = self.tmp / "libmain.so"
        lib.write_bytes(b"\x7fELF" + b"\0" * 16)
        stage = package.embed(apk, dex_dir=dex_dir, native_libs={"arm64-v8a": (lib,)})
        self.assertEqual(stage.state, stages.DONE)
        with zipfile.ZipFile(apk) as archive:
            names = archive.namelist()
        self.assertIn("classes.dex", names)
        self.assertIn("lib/arm64-v8a/libmain.so", names)

    def test_embed_nothing_is_unsupported(self) -> None:
        apk = write_apk(self.tmp / "base.apk")
        self.assertEqual(package.embed(apk).state, stages.UNSUPPORTED)

    # ----------------------------------------------------------- sign / verify

    def test_sign_missing_apk_fails(self) -> None:
        stage = package.sign("/fake/apksigner", self.tmp / "nope.apk", self.tmp / "out.apk", keystore=self.tmp / "k.p12", keystore_pass="x")
        self.assertEqual(stage.state, stages.FAILED)

    def test_sign_missing_keystore_is_unsupported(self) -> None:
        apk = write_apk(self.tmp / "base.apk")
        stage = package.sign("/fake/apksigner", apk, self.tmp / "out.apk", keystore=self.tmp / "missing.p12", keystore_pass="x")
        self.assertEqual(stage.state, stages.UNSUPPORTED)

    def test_sign_passes_min_sdk_when_set(self) -> None:
        apk = write_apk(self.tmp / "base.apk")
        keystore = self.tmp / "debug.p12"
        keystore.write_bytes(b"jks")
        out = self.tmp / "out.apk"
        self.fake.rule("apksigner", effect=lambda argv: Path(_value_after(argv, "--out")).write_bytes(apk.read_bytes()))
        stage = package.sign("/fake/apksigner", apk, out, keystore=keystore, keystore_pass="pw", min_sdk=24)
        self.assertEqual(stage.state, stages.DONE)
        self.assertEqual(_value_after(self.fake.last("sign"), "--min-sdk-version"), "24")

    def test_verify_missing_apk_fails(self) -> None:
        self.assertEqual(package.verify("/fake/apksigner", self.tmp / "nope.apk").state, stages.FAILED)

    # ----------------------------------------------------------- selftest path

    def test_selftest_reports_missing_tools_without_claiming_success(self) -> None:
        # Patch the probes to report the tools missing, then confirm no artifact.
        original = env.probe
        env.probe = lambda name, timeout=20: env.Tool(name=name, path=None, state="missing", error="not on PATH")
        try:
            result = package.selftest(self.tmp / "selftest")
        finally:
            env.probe = original
        self.assertFalse(result["ok"])
        self.assertTrue(result["missing"])
        self.assertNotIn("artifact", result)


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-apk-validate-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inspect_missing_file(self) -> None:
        report = validate.inspect(self.tmp / "nope.apk")
        self.assertFalse(report["ok"])

    def test_inspect_not_a_zip(self) -> None:
        path = self.tmp / "plain.apk"
        path.write_bytes(b"not a zip")
        report = validate.inspect(path)
        self.assertFalse(report["ok"])

    def test_inspect_reads_package_dex_and_abis(self) -> None:
        path = write_apk(self.tmp / "app.apk")
        report = validate.inspect(path)
        self.assertTrue(report["ok"])
        self.assertEqual(report["package"], "com.example.porttest")
        self.assertTrue(report["dex_magic_ok"])
        self.assertIn("arm64-v8a", report["abis"])
        self.assertEqual(report["native_libraries"], 1)

    def test_inspect_flags_bad_dex_magic(self) -> None:
        path = write_apk(self.tmp / "bad.apk", dex_magic=b"nope")
        report = validate.inspect(path)
        self.assertFalse(report["ok"])
        self.assertFalse(report["dex_magic_ok"])

    def test_inspect_flags_duplicate_entries(self) -> None:
        import warnings

        path = self.tmp / "dup.apk"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("AndroidManifest.xml", make_manifest_axml())
                archive.writestr("classes.dex", b"dex\n035\0")
                archive.writestr("classes.dex", b"dex\n035\0")
        report = validate.inspect(path)
        self.assertFalse(report["ok"])

    def test_inspect_flags_missing_manifest(self) -> None:
        path = self.tmp / "nomanifest.apk"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("classes.dex", b"dex\n035\0")
        report = validate.inspect(path)
        self.assertFalse(report["ok"])

    def test_check_package_name(self) -> None:
        path = write_apk(self.tmp / "app.apk")
        self.assertTrue(validate.check_package_name(path, "com.example.porttest")["ok"])
        self.assertFalse(validate.check_package_name(path, "com.other")["ok"])

    def test_render_contains_verdict(self) -> None:
        text = validate.render(validate.inspect(write_apk(self.tmp / "app.apk")))
        self.assertIn("OK", text)
        self.assertIn("package", text)


class SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeProc()
        self._run = proc_module.run
        self._probe = env.probe
        proc_module.run = self.fake

    def tearDown(self) -> None:
        proc_module.run = self._run
        env.probe = self._probe

    def _adb_ok(self) -> None:
        env.probe = lambda name, timeout=20: env.Tool(name=name, path="/fake/adb", version="1.0.41", state="ok")

    def _adb_broken(self) -> None:
        env.probe = lambda name, timeout=20: env.Tool(name=name, path="/fake/adb", state="broken", error="CANNOT LINK EXECUTABLE")

    def test_devices_unsupported_when_adb_broken(self) -> None:
        self._adb_broken()
        self.assertEqual(smoke.devices().state, stages.UNSUPPORTED)
        self.assertEqual(smoke.install("/fake.apk").state, stages.UNSUPPORTED)

    def test_devices_ok(self) -> None:
        self._adb_ok()
        self.fake.rule("adb", stdout="List of devices attached\nemulator-5554\tdevice\n")
        stage = smoke.devices()
        self.assertEqual(stage.state, stages.DONE)
        self.assertIn("emulator-5554", stage.detail)

    def test_devices_none_attached_fails(self) -> None:
        self._adb_ok()
        self.fake.rule("adb", stdout="List of devices attached\n")
        self.assertEqual(smoke.devices().state, stages.FAILED)

    def test_install_requires_success(self) -> None:
        self._adb_ok()
        apk = self.tmp_file()
        self.fake.rule("install", stdout="Success\n")
        self.assertEqual(smoke.install(apk).state, stages.DONE)
        self.fake.rules = []
        self.fake.rule("install", stdout="Failure [INSTALL_FAILED_ALREADY_EXISTS]\n", returncode=1)
        self.assertEqual(smoke.install(apk).state, stages.FAILED)

    def test_logcat_flags_crash(self) -> None:
        self._adb_ok()
        lines = "\n".join(
            [
                "D/AndroidRuntime( 1234): Shutting down VM",
                "E/AndroidRuntime( 1234): FATAL EXCEPTION: main",
            ]
        )
        self.fake.rule("logcat", stdout=lines + "\n")
        self.assertEqual(smoke.logcat().state, stages.FAILED)

    def test_logcat_ok_with_lines(self) -> None:
        self._adb_ok()
        self.fake.rule("logcat", stdout="I/Unity( 1234): starting up\n" * 5)
        self.assertEqual(smoke.logcat().state, stages.DONE)

    def test_logcat_filters_by_package(self) -> None:
        self._adb_ok()
        self.fake.rule("logcat", stdout="I/Unity( 1234): starting dev.x\nI/Other( 1): noise\n")
        self.assertEqual(smoke.logcat(package="dev.x").state, stages.DONE)

    def test_logcat_no_lines_fails(self) -> None:
        self._adb_ok()
        self.fake.rule("logcat", stdout="")
        self.assertEqual(smoke.logcat().state, stages.FAILED)

    def test_state_reports_broken(self) -> None:
        self._adb_broken()
        self.assertEqual(smoke.state()["state"], "broken")

    def tmp_file(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="pa-smoke-")) / "app.apk"
        path.write_bytes(b"PK")
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        return path


class RustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-rust-"))
        self.fake = FakeProc()
        self._run = proc_module.run
        self._probe = env.probe
        proc_module.run = self.fake

    def tearDown(self) -> None:
        proc_module.run = self._run
        env.probe = self._probe
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tools_ok(self, libdir: str | None) -> None:
        def probe(name, timeout=20):
            if name == "cargo":
                return env.Tool(name=name, path="/fake/cargo", version="1.98", state="ok")
            if name == "rustc":
                return env.Tool(name=name, path="/fake/rustc", version="1.98", state="ok")
            return env.Tool(name=name, path=None, state="missing")

        env.probe = probe
        self.libdir = self.tmp / "libdir"
        if libdir is not None:
            Path(libdir).mkdir(parents=True, exist_ok=True)
        self.fake.rule("--print", stdout=(libdir or "/nonexistent/nowhere") + "\n")

    def test_bad_abi_fails(self) -> None:
        project = self.tmp / "p"
        project.mkdir()
        (project / "Cargo.toml").write_text("[package]\nname='p'\nversion='0.0.0'\n")
        self.assertEqual(rust.build(project, abi="mips")["stage"]["state"], stages.FAILED)

    def test_missing_cargo_manifest_fails(self) -> None:
        self.assertEqual(rust.build(self.tmp / "empty")["stage"]["state"], stages.FAILED)

    def test_missing_target_is_unsupported(self) -> None:
        project = self.tmp / "p"
        project.mkdir()
        (project / "Cargo.toml").write_text("[package]\nname='p'\nversion='0.0.0'\n")
        self._tools_ok(None)  # libdir left uncreated -> target not installed
        result = rust.build(project)
        self.assertEqual(result["stage"]["state"], stages.UNSUPPORTED)
        self.assertIn("rustup target add", result["stage"]["detail"])

    def test_build_produces_library(self) -> None:
        project = self.tmp / "p"
        project.mkdir()
        (project / "Cargo.toml").write_text("[package]\nname='p'\nversion='0.0.0'\n")
        self._tools_ok(str(self.tmp / "libdir"))
        out_dir = project / "target" / "aarch64-linux-android" / "release"

        def effect(argv):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "libp.so").write_bytes(b"\x7fELF")

        self.fake.rule("cargo", effect=effect)
        result = rust.build(project, abi="arm64-v8a")
        self.assertEqual(result["stage"]["state"], stages.DONE)
        self.assertTrue(result["libraries"]["arm64-v8a"])

    def test_cargo_success_without_so_fails(self) -> None:
        project = self.tmp / "p"
        project.mkdir()
        (project / "Cargo.toml").write_text("[package]\nname='p'\nversion='0.0.0'\n")
        self._tools_ok(str(self.tmp / "libdir"))
        self.fake.rule("cargo")  # exits 0 but leaves no .so
        self.assertEqual(rust.build(project)["stage"]["state"], stages.FAILED)


class CppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-cpp-"))
        self.fake = FakeProc()
        self._run = proc_module.run
        self._probe = env.probe
        self._find_ndk = env.find_ndk
        proc_module.run = self.fake
        env.probe = lambda name, timeout=20: env.Tool(name=name, path=f"/fake/{name}", version="1.0", state="ok")

    def tearDown(self) -> None:
        proc_module.run = self._run
        env.probe = self._probe
        env.find_ndk = self._find_ndk
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_configure_requires_cmakelists(self) -> None:
        stage = cpp.configure(self.tmp / "src", self.tmp / "build")
        self.assertEqual(stage.state, stages.FAILED)

    def test_android_libraries_without_ndk_is_unsupported(self) -> None:
        env.find_ndk = lambda: {"root": None, "toolchain_file": None}
        result = cpp.android_libraries(self.tmp / "src", self.tmp / "build", abi="arm64-v8a")
        self.assertEqual(result["stages"][0]["state"], stages.UNSUPPORTED)
        self.assertEqual(result["libraries"], {})

    def test_build_requires_configured_tree(self) -> None:
        self.assertEqual(cpp.build(self.tmp / "not-configured").state, stages.FAILED)

    def test_collect_shared_skips_cmakefiles(self) -> None:
        build = self.tmp / "build"
        (build / "CMakeFiles").mkdir(parents=True)
        (build / "CMakeFiles" / "x.so").write_bytes(b"x")
        (build / "libout.so").write_bytes(b"x")
        found = [item.name for item in cpp.collect_shared(build)]
        self.assertEqual(found, ["libout.so"])


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-pipeline-"))
        self.fake = FakeProc()
        self._run = proc_module.run
        self._detect = env.detect
        proc_module.run = self.fake

    def tearDown(self) -> None:
        proc_module.run = self._run
        env.detect = self._detect
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _environment(self, *, tools_ok: bool) -> dict:
        tools = {}
        for name in env.VERSION_ARGS:
            tools[name] = {
                "name": name,
                "path": f"/fake/{name}",
                "version": "1.0",
                "state": "ok" if tools_ok else "missing",
                "error": "",
            }
        return {
            "tools": tools,
            "android": {
                "sdk_root": None,
                "framework_jar": None,
                "api": None,
                "ndk_root": None,
                "ndk_toolchain_file": None,
                "abis": list(env.ANDROID_ABIS),
                "adb": tools["adb"],
                "adb_reason": "",
            },
            "capabilities": {},
        }

    def test_toolchain_missing_short_circuits(self) -> None:
        env.detect = lambda: self._environment(tools_ok=False)
        spec = package.ProjectSpec(manifest=self.tmp / "AndroidManifest.xml")
        report = pipeline.run(spec, check_device=False)
        self.assertEqual(report.stages[0].state, stages.UNSUPPORTED)
        self.assertTrue(report.ok)  # unsupported is not failed
        self.assertIsNone(report.artifact)
        self.assertFalse(report.claims["port_verified"])
        self.assertFalse(report.claims["artifact_exists"])

    def test_full_path_claims_are_honest(self) -> None:
        env.detect = lambda: self._environment(tools_ok=True)
        manifest = self.tmp / "AndroidManifest.xml"
        manifest.write_text("<manifest/>", encoding="utf-8")
        class_file = self.tmp / "A.class"
        class_file.write_bytes(b"\xca\xfe\xba\xbe")
        keystore = self.tmp / "debug.p12"
        keystore.write_bytes(b"jks")

        def link_effect(argv):
            out = Path(_value_after(argv, "-o"))
            with zipfile.ZipFile(out, "w") as archive:
                archive.writestr("AndroidManifest.xml", make_manifest_axml("dev.pipeline"))

        def dex_effect(argv):
            directory = Path(_value_after(argv, "--output"))
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "classes.dex").write_bytes(b"dex\n035\0")

        self.fake.rule("aapt2", effect=link_effect)
        self.fake.rule("d8", effect=dex_effect)
        self.fake.rule("apksigner sign", effect=lambda argv: Path(_value_after(argv, "--out")).write_bytes(Path(argv[-1]).read_bytes()))
        self.fake.rule("apksigner verify", returncode=0, stdout="Verifies\n")

        spec = package.ProjectSpec(
            manifest=manifest,
            out=self.tmp / "build" / "app-unsigned.apk",
            classes=(class_file,),
            keystore=keystore,
            keystore_pass="pw",
            package_name="dev.pipeline",
        )
        report = pipeline.run(spec, strategy_id="native_port", check_device=False)
        claims = report.claims
        self.assertTrue(claims["artifact_exists"])
        self.assertTrue(claims["signed"])
        self.assertFalse(claims["installed"])
        self.assertFalse(claims["runtime_verified"])
        # The whole point: packaging never implies a port.
        self.assertFalse(claims["port_verified"])
        self.assertEqual(claims["package_name"], "dev.pipeline")
        self.assertIn("inspect", [stage.id for stage in report.stages])

    def test_pipeline_strategy_is_recorded(self) -> None:
        env.detect = lambda: self._environment(tools_ok=False)
        spec = package.ProjectSpec(manifest=self.tmp / "AndroidManifest.xml")
        report = pipeline.run(spec, strategy_id="emulator", check_device=False)
        self.assertEqual(report.strategy, "emulator")
        self.assertEqual(report.as_dict()["strategy"], "emulator")

    def test_unknown_strategy_raises(self) -> None:
        spec = package.ProjectSpec(manifest=self.tmp / "AndroidManifest.xml")
        with self.assertRaises(ValueError):
            pipeline.run(spec, strategy_id="nope")


class HostIntegrationTests(unittest.TestCase):
    """Runs only when the host has the real packaging tools."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = {name: env.probe(name) for name in ("aapt2", "d8", "apksigner", "javac", "keytool")}
        cls.available = all(tool.ok for tool in cls.tools.values())

    def test_selftest_end_to_end(self) -> None:
        if not self.available:
            self.skipTest("aapt2/d8/apksigner/javac/keytool not all present")
        result = package.selftest()
        self.assertTrue(result["ok"], msg=[stage for stage in result["stages"] if stage["state"] != "done"])
        try:
            report = validate.inspect(result["artifact"])
            self.assertTrue(report["ok"])
            self.assertEqual(report["package"], "dev.portanything.selftest")
        finally:
            package.clean(result["workdir"])


if __name__ == "__main__":
    unittest.main()
