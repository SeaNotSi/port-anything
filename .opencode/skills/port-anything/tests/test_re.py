"""Unit and integration tests for the reverse-engineering module."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))
sys.path.insert(0, str(SKILL_ROOT / "tests" / "fixtures"))

from make_fixtures import generate_all  # noqa: E402

from pa.re import analyze, assess, render_markdown, render_text, sniff  # noqa: E402
from pa.re.axml import parse_axml  # noqa: E402


class ReverseEngineeringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="pa-re-"))
        cls.fixtures = generate_all(cls.tmp)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def analyze_fixture(self, name: str) -> dict:
        return analyze(self.fixtures[name])

    # ------------------------------------------------------------------ sniff

    def test_sniff_formats(self) -> None:
        expected = {
            "pe": "pe",
            "macho": "macho",
            "apk": "apk",
            "aab": "aab",
            "unity": "unity_assetbundle",
            "godot_pck": "godot_pck",
            "gamemaker": "gamemaker_datawin",
            "renpy": "renpy_rpa",
            "love": "love_archive",
            "unreal_pak": "ue_pak",
            "iso": "iso9660",
        }
        for name, fmt in expected.items():
            with self.subTest(fixture=name):
                self.assertEqual(sniff(self.fixtures[name]).format_id, fmt)

    def test_sniff_unknown_is_explicit(self) -> None:
        path = self.tmp / "random.bin"
        path.write_bytes(b"\x13\x37" * 512)
        guess = sniff(path)
        self.assertEqual(guess.format_id, "unknown")
        self.assertFalse(guess.known)

    # --------------------------------------------------------------- analyzers

    def test_pe_analyzer_reads_imports(self) -> None:
        bundle = self.analyze_fixture("pe")
        pe = next(entry for entry in bundle["analyzers"] if entry["analyzer"] == "pe")
        self.assertIn("UnityPlayer.dll", pe["result"]["imported_dlls"])
        self.assertEqual(pe["result"]["machine"], "x86-64")
        self.assertTrue(pe["result"]["pe32_plus"])

    def test_macho_analyzer_reads_platform(self) -> None:
        bundle = self.analyze_fixture("macho")
        macho = next(entry for entry in bundle["analyzers"] if entry["analyzer"] == "macho")
        self.assertEqual(macho["result"]["cputype"], "ARM64")
        self.assertEqual(macho["result"]["build"]["platform"], "macos")
        self.assertIn("UnityPlayer.dylib", macho["result"]["dylibs"])

    def test_apk_manifest_and_abis(self) -> None:
        bundle = self.analyze_fixture("apk")
        apk = next(entry for entry in bundle["analyzers"] if entry["analyzer"] == "apk")["result"]
        manifest = apk["manifest"]
        self.assertEqual(manifest["package"], "com.example.porttest")
        self.assertEqual(manifest["version_code"], 7)
        self.assertEqual(manifest["min_sdk"], 24)
        self.assertEqual(manifest["target_sdk"], 34)
        self.assertIn("android.permission.INTERNET", manifest["permissions"])
        self.assertIn("com.example.porttest.MainActivity", manifest["activities"])
        self.assertEqual(apk["abis"], ["arm64-v8a", "armeabi-v7a"])
        self.assertIn("unity", apk["engine_hints"])
        self.assertTrue(apk["has_v1_signature"])

    def test_axml_parser_elements(self) -> None:
        from make_fixtures import make_manifest_axml

        parsed = parse_axml(make_manifest_axml())
        names = [element["name"] for element in parsed["elements"]]
        self.assertIn("manifest", names)
        self.assertIn("application", names)
        self.assertGreaterEqual(parsed["element_count"], 5)

    def test_game_containers(self) -> None:
        unity = next(e for e in self.analyze_fixture("unity")["analyzers"] if e["analyzer"] == "unity_bundle")
        self.assertEqual(unity["result"]["unity_version"], "2019.4.0f1")
        self.assertEqual(unity["result"]["compression"], "lz4")

        godot = next(e for e in self.analyze_fixture("godot_pck")["analyzers"] if e["analyzer"] == "godot_pck")
        self.assertEqual(godot["result"]["engine_version"], "4.2.1")

        gamemaker = next(e for e in self.analyze_fixture("gamemaker")["analyzers"] if e["analyzer"] == "gamemaker_data")
        self.assertEqual(gamemaker["result"]["chunks"][0]["name"], "STRG")

        love = next(e for e in self.analyze_fixture("love")["analyzers"] if e["analyzer"] == "love_archive")
        self.assertTrue(love["result"]["has_main_lua"])

    def test_analyzer_failure_is_contained(self) -> None:
        path = self.tmp / "broken.bundle"
        path.write_bytes(b"UnityFS\0")
        bundle = analyze(path)
        entry = next(item for item in bundle["analyzers"] if item["analyzer"] == "unity_bundle")
        self.assertIn("error", entry)
        self.assertTrue(bundle["fingerprint"]["engines"])  # container evidence still recorded

    # ------------------------------------------------------------ fingerprint

    def test_fingerprint_unity_pe(self) -> None:
        fp = self.analyze_fixture("pe")["fingerprint"]
        self.assertEqual(fp["top_engine"], "unity")
        self.assertEqual(fp["platforms"], ["windows"])
        self.assertEqual(fp["architectures"], ["x86_64"])
        evidence_sources = {item["source"] for item in fp["engines"][0]["evidence"]}
        self.assertIn("binary_import", evidence_sources)
        self.assertIn("marker", evidence_sources)

    def test_fingerprint_apk(self) -> None:
        fp = self.analyze_fixture("apk")["fingerprint"]
        self.assertEqual(fp["top_engine"], "unity")
        self.assertEqual(fp["platforms"], ["android"])
        self.assertEqual(fp["architectures"], ["arm32", "arm64"])
        self.assertTrue(fp["android_runtime"])
        self.assertIn("apk_assets", fp["assets"])

    def test_fingerprint_source_projects(self) -> None:
        cpp = self.analyze_fixture("cpp_project")["fingerprint"]
        self.assertTrue(cpp["source_available"])
        self.assertEqual(cpp["build_systems"], ["cmake"])
        self.assertIn("cpp", cpp["languages"])

        godot = self.analyze_fixture("godot_project")["fingerprint"]
        self.assertEqual(godot["top_engine"], "godot")
        self.assertEqual(godot["engines"][0]["confidence"], "certain")

    def test_fingerprint_unknown_notes_packing(self) -> None:
        path = self.tmp / "random2.bin"
        path.write_bytes(b"\x00\x01\x02\x03" * 256)
        fp = analyze(path)["fingerprint"]
        self.assertIsNone(fp["top_engine"])
        self.assertTrue(any("packed" in note for note in fp["notes"]))

    # --------------------------------------------------------------- compat

    def test_compat_apk_is_not_a_port(self) -> None:
        bundle = self.analyze_fixture("apk")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "already_android")
        self.assertTrue(compat["claims"]["apk_present"])
        self.assertFalse(compat["claims"]["port_verified"])
        self.assertFalse(compat["claims"]["port_complete"])
        stages = {stage["id"]: stage for stage in compat["strategies"][0]["stages"]}
        self.assertEqual(stages["toolchain"]["status"], "unknown")

    def test_compat_godot_source(self) -> None:
        bundle = self.analyze_fixture("godot_project")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "engine_recompile")
        stages = {stage["id"]: stage for stage in compat["strategies"][0]["stages"]}
        self.assertEqual(stages["source"]["status"], "done")

    def test_compat_cpp_source(self) -> None:
        bundle = self.analyze_fixture("cpp_project")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "native_port")

    def test_compat_iso_emulator(self) -> None:
        bundle = self.analyze_fixture("iso")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "emulator")

    def test_compat_capabilities_drive_stages(self) -> None:
        bundle = self.analyze_fixture("apk")
        caps = {
            "apk_gradle": "supported",
            "cpp_android_build": "supported",
            "apk_fallback": "supported",
            "device_test": "unsupported",
        }
        compat = assess(bundle["fingerprint"], capabilities=caps)
        stages = {stage["id"]: stage for stage in compat["strategies"][0]["stages"]}
        self.assertEqual(stages["toolchain"]["status"], "ready")
        self.assertEqual(stages["package"]["status"], "ready")
        self.assertEqual(stages["install"]["status"], "blocked")
        self.assertTrue(any("adb" in blocker for blocker in compat["blockers"]))

    def test_compat_dataset_facts_when_index_present(self) -> None:
        bundle = self.analyze_fixture("godot_pck")
        compat = assess(bundle["fingerprint"], capabilities=None)
        if not compat["dataset"]["available"]:
            self.skipTest("dataset index not built; run: pa dataset index")
        facts = compat["dataset"]["engine_facts"]
        self.assertEqual(facts["record"], "engine.godot")
        self.assertEqual(facts["android"], "supported")

    def test_asset_only_recommends_migration(self) -> None:
        bundle = self.analyze_fixture("godot_pck")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "asset_migration")

    def test_windows_binary_recommends_compatibility_layer(self) -> None:
        bundle = self.analyze_fixture("pe")
        compat = assess(bundle["fingerprint"], capabilities=None)
        self.assertEqual(compat["recommended"], "compatibility_layer")

    # ---------------------------------------------------------------- report

    def test_render_text_and_markdown(self) -> None:
        bundle = self.analyze_fixture("apk")
        bundle["compatibility"] = assess(bundle["fingerprint"], capabilities=None)
        text = render_text(bundle)
        markdown = render_markdown(bundle)
        self.assertIn("format    : apk", text)
        self.assertIn("port_verified=false", text)
        self.assertIn("## Port routes", markdown)
        self.assertIn("port_verified=false", markdown)


if __name__ == "__main__":
    unittest.main()
