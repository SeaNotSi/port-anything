"""Unit and regression tests for the token-efficiency module."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from pa.tokens import cache, config, context, duplicate, estimate, guard, sources  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class EstimateTests(unittest.TestCase):
    def test_ascii_text(self) -> None:
        result = estimate.count_text("hello world this is a test")
        self.assertGreater(result.tokens, 0)
        self.assertEqual(result.lines, 1)
        self.assertEqual(result.confidence, "±10%")

    def test_cjk_costs_one_token_per_character(self) -> None:
        text = "游戏移植框架"
        result = estimate.count_text(text)
        self.assertEqual(result.tokens, len(text))
        self.assertEqual(result.confidence, "±20%")

    def test_whitespace_is_free(self) -> None:
        tight = estimate.count_text("abcdefgh")
        loose = estimate.count_text("a b c d e f g h")
        self.assertEqual(tight.tokens, loose.tokens)

    def test_empty(self) -> None:
        result = estimate.count_text("")
        self.assertEqual(result.tokens, 0)
        self.assertEqual(result.lines, 0)

    def test_file_estimate_matches_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "a.txt", "one\ntwo\nthree\n")
            result = estimate.estimate_file(path)
            self.assertEqual(result["lines"], 3)
            self.assertTrue(result["complete"])
            self.assertFalse(result["binary"])

    def test_binary_file_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.bin"
            path.write_bytes(b"\0\x01\x02")
            result = estimate.estimate_file(path)
            self.assertTrue(result["binary"])

    def test_config_rejects_bad_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text('{"chars_per_token": -1, "max_files": "no", "unknown": 5}', encoding="utf-8")
            cfg = config.load(path)
            self.assertEqual(cfg["chars_per_token"], config.DEFAULTS["chars_per_token"])
            self.assertEqual(cfg["max_files"], config.DEFAULTS["max_files"])
            self.assertNotIn("unknown", cfg)


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-cache-"))
        self._original = cache.CACHE_DIR
        cache.CACHE_DIR = self.tmp

    def tearDown(self) -> None:
        cache.CACHE_DIR = self._original
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get_roundtrip(self) -> None:
        cache.put("k", "payload", {"value": 42})
        self.assertEqual(cache.get("k", "payload"), {"value": 42})

    def test_different_payload_misses(self) -> None:
        cache.put("k", "one", {"value": 1})
        self.assertIsNone(cache.get("k", "two"))

    def test_different_kind_misses(self) -> None:
        cache.put("a", "same", {"value": 1})
        self.assertIsNone(cache.get("b", "same"))

    def test_memo_runs_once(self) -> None:
        calls = {"count": 0}

        def producer() -> dict:
            calls["count"] += 1
            return {"value": calls["count"]}

        first = cache.memo("memo", "x", producer)
        second = cache.memo("memo", "x", producer)
        self.assertEqual(first, second)
        self.assertEqual(calls["count"], 1)

    def test_stats_and_clear(self) -> None:
        cache.put("k", "payload", {"value": 1})
        self.assertEqual(cache.stats()["entries"], 1)
        self.assertEqual(cache.clear(), 1)
        self.assertEqual(cache.stats()["entries"], 0)


class SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-src-"))
        _write(self.tmp / "src" / "main.cpp", "int main() {\n  return 0;\n}\n")
        _write(self.tmp / "README.md", "# Fixture\n")
        (self.tmp / "blob.bin").write_bytes(b"\0\x01")
        self.index = sources.index([self.tmp])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_indexes_text_and_skips_binary(self) -> None:
        paths = {source.path for source in self.index.files}
        self.assertIn("src/main.cpp", paths)
        self.assertIn("README.md", paths)
        self.assertNotIn("blob.bin", paths)
        self.assertTrue(any(item["reason"] == "binary" for item in self.index.skipped))

    def test_line_counts_and_hashes(self) -> None:
        source = self.index.by_path("src/main.cpp")
        assert source is not None
        self.assertEqual(source.lines, 3)
        self.assertEqual(source.language, "cpp")
        self.assertEqual(len(source.sha256), 64)
        self.assertTrue(self.index.verify(source.id))

    def test_manifest_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = sources.save_manifest(self.index, Path(tmp) / "manifest.json")
            loaded = sources.load_manifest(path)
        self.assertEqual(loaded.manifest_hash(), self.index.manifest_hash())
        self.assertEqual(len(loaded.files), len(self.index.files))

    def test_read_slice_bounds(self) -> None:
        source = self.index.by_path("src/main.cpp")
        assert source is not None
        self.assertEqual(self.index.read_slice(source.id, 1, 1), "int main() {\n")
        with self.assertRaises(ValueError):
            self.index.read_slice(source.id, 0, 1)
        with self.assertRaises(ValueError):
            self.index.read_slice(source.id, 1, source.lines + 1)
        with self.assertRaises(KeyError):
            self.index.read_slice("src-9999", 1, 1)

    def test_search_returns_ranges(self) -> None:
        hits = self.index.search("return")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["line"], 2)
        self.assertLessEqual(hits[0]["start_line"], 2)
        self.assertGreaterEqual(hits[0]["end_line"], 2)


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-plan-"))
        self.big = _write(
            self.tmp / "big.py",
            "\n".join(f"def function_{index}():\n    return {index}\n" for index in range(200)),
        )
        self.small = _write(self.tmp / "small.py", "value = 1\n")
        self.duplicate = _write(self.tmp / "copy.py", "value = 1\n")
        self.index = sources.index([self.tmp], config={**config.DEFAULTS, "cache_enabled": False})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_when_budget_allows(self) -> None:
        plan = context.plan(self.index, budget=100000)
        modes = {item["path"]: item["mode"] for item in plan["items"]}
        self.assertIn(modes["big.py"], ("full",))
        duplicate_modes = sorted({modes["small.py"], modes["copy.py"]})
        self.assertEqual(duplicate_modes, ["alias", "full"])
        self.assertEqual(plan["coverage"], 1.0)
        self.assertEqual(guard.check(plan, self.index)["ok"], True)

    def test_windows_and_omitted_when_tight(self) -> None:
        plan = context.plan(self.index, budget=120, query="function_42")
        big_items = [item for item in plan["items"] if item["path"] == "big.py"]
        self.assertTrue(big_items)
        self.assertIn(big_items[0]["mode"], ("window", "index"))
        big_gaps = [gap for gap in plan["omitted"] if gap["path"] == "big.py"]
        self.assertTrue(big_gaps)
        report = guard.check(plan, self.index)
        self.assertTrue(report["ok"], report["violations"])
        self.assertLessEqual(report["recomputed_tokens"], 120)

    def test_zero_budget_indexes_everything(self) -> None:
        plan = context.plan(self.index, budget=0)
        self.assertTrue(all(item["mode"] in ("index", "alias") for item in plan["items"]))
        self.assertEqual(plan["total_tokens"], 0)
        self.assertTrue(guard.check(plan, self.index)["ok"])

    def test_alias_dedupe(self) -> None:
        plan = context.plan(self.index, budget=100000, dedupe=True)
        aliases = [item for item in plan["items"] if item["mode"] == "alias"]
        self.assertEqual(len(aliases), 1)
        self.assertIn(aliases[0]["path"], ("copy.py", "small.py"))

    def test_must_keep_is_prioritised(self) -> None:
        target = self.index.by_path("small.py")
        assert target is not None
        plan = context.plan(self.index, budget=40, must_keep=[target.id])
        first = plan["items"][0]
        self.assertEqual(first["source_id"], target.id)

    def test_guard_detects_truncated_text(self) -> None:
        plan = context.plan(self.index, budget=100000)
        full = next(item for item in plan["items"] if item["mode"] == "full" and item["path"] == "big.py")
        full["text"] = ""
        report = guard.check(plan, self.index)
        self.assertFalse(report["ok"])
        self.assertTrue(any("lines of text" in violation for violation in report["violations"]))

    def test_guard_detects_missing_omitted(self) -> None:
        plan = context.plan(self.index, budget=0)
        plan["omitted"] = []
        report = guard.check(plan, self.index)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(("unaccounted" in violation) or ("not accounted" in violation) for violation in report["violations"])
        )

    def test_guard_detects_budget_lie(self) -> None:
        plan = context.plan(self.index, budget=100000)
        plan["total_tokens"] = 0
        report = guard.check(plan, self.index)
        self.assertFalse(report["ok"])

    def test_regression_across_budgets(self) -> None:
        for budget in (0, 10, 50, 200, 800, 5000, 100000):
            with self.subTest(budget=budget):
                plan = context.plan(self.index, budget=budget, query="function_7")
                report = guard.check(plan, self.index, budget=budget)
                self.assertTrue(report["ok"], (budget, report["violations"]))
                self.assertLessEqual(report["recomputed_tokens"], budget)

    def test_render_plan_marks_omissions(self) -> None:
        plan = context.plan(self.index, budget=60)
        text = context.render_plan(plan)
        self.assertIn("# Context plan", text)
        self.assertIn("Omitted ranges", text)


class DuplicateTests(unittest.TestCase):
    def test_exact_and_near(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="pa-dup-"))
        try:
            body = "\n".join(f"line_{index} = {index}" for index in range(40)) + "\n"
            _write(tmp / "a.py", body)
            _write(tmp / "b.py", body)
            _write(tmp / "c.py", body + "extra = 1\n")
            result = duplicate.find_duplicates([tmp], config={**config.DEFAULTS, "cache_enabled": False})
            self.assertEqual(len(result["exact"]), 1)
            self.assertEqual(len(result["exact"][0]["sources"]), 2)
            self.assertGreater(result["savings_tokens"], 0)
            pairs = {(pair["path_a"], pair["path_b"]) for pair in result["near"]}
            self.assertTrue(any("a.py" in pair[0] and "b.py" in pair[1] for pair in pairs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()