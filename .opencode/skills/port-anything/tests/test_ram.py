"""Unit and regression tests for the RAM-efficiency module."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from pa.ram import cache, config, manager, policy  # noqa: E402

GIB = 1 << 30


def snapshot(available: int, total: int = 8 * GIB, cpus: int = 8, load1: float = 0.1) -> dict:
    return {
        "ts": time.time(),
        "total": total,
        "available": available,
        "used": total - available,
        "percent_used": round((total - available) / total, 4),
        "cpus": cpus,
        "load1": load1,
        "load5": load1,
        "load15": load1,
        "cgroup_limit": None,
        "cgroup_used": None,
        "self_rss": 1024,
    }


def base_config(**overrides: object) -> dict:
    cfg = {
        **config.DEFAULTS,
        "concurrency": dict(config.DEFAULTS["concurrency"]),
        "sample_interval_seconds": 0.02,
    }
    cfg.update(overrides)
    return cfg


class ConfigTests(unittest.TestCase):
    def test_defaults_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(Path(tmp) / "absent.json")
        self.assertEqual(cfg["concurrency"], {"low": 1, "medium": 2, "high": 3})
        self.assertEqual(cfg["min_free_bytes"], config.DEFAULTS["min_free_bytes"])

    def test_bad_values_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(
                json.dumps(
                    {
                        "min_free_bytes": 0,
                        "low_available_ratio": -0.2,
                        "max_concurrency": "eight",
                        "unknown_key": 7,
                    }
                ),
                encoding="utf-8",
            )
            cfg = config.load(path)
        self.assertEqual(cfg["min_free_bytes"], config.DEFAULTS["min_free_bytes"])
        self.assertEqual(cfg["low_available_ratio"], config.DEFAULTS["low_available_ratio"])
        self.assertEqual(cfg["max_concurrency"], config.DEFAULTS["max_concurrency"])
        self.assertNotIn("unknown_key", cfg)

    def test_invalid_concurrency_map_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"concurrency": {"low": 2}}), encoding="utf-8")
            cfg = config.load(path)
        self.assertEqual(cfg["concurrency"], config.DEFAULTS["concurrency"])

    def test_unordered_bands_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(
                json.dumps({"low_available_ratio": 0.6, "medium_available_ratio": 0.2}),
                encoding="utf-8",
            )
            cfg = config.load(path)
        self.assertLess(cfg["low_available_ratio"], cfg["medium_available_ratio"])

    def test_cpu_reserve_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"reserve_cpu_factor": 2.0}), encoding="utf-8")
            cfg = config.load(path)
        self.assertEqual(cfg["reserve_cpu_factor"], config.DEFAULTS["reserve_cpu_factor"])

    def test_shipped_config_is_valid(self) -> None:
        cfg = config.load()
        self.assertIn(cfg["concurrency"], (config.DEFAULTS["concurrency"], cfg["concurrency"]))
        self.assertLess(cfg["low_available_ratio"], cfg["medium_available_ratio"])
        self.assertLess(cfg["min_free_bytes"], cfg["low_available_bytes"])


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = base_config()

    def test_unavailable_sample_degrades_to_low(self) -> None:
        level, reasons = policy.classify({"available": None, "total": 0}, self.cfg)
        self.assertEqual(level, "low")
        self.assertIn("unavailable", reasons[0])

    def test_below_reserve_is_low(self) -> None:
        level, _ = policy.classify(snapshot(self.cfg["min_free_bytes"] - 1), self.cfg)
        self.assertEqual(level, "low")

    def test_low_ratio_band(self) -> None:
        total = 8 * GIB
        available = int(total * 0.05)
        level, reasons = policy.classify(snapshot(available, total), self.cfg)
        self.assertEqual(level, "low")
        self.assertIn("low band", reasons[0])

    def test_absolute_floor_overrides_ratio(self) -> None:
        # 12% of 3 GiB is 368 MiB: ratio says medium, the floor says low.
        total = 3 * GIB
        available = int(total * 0.12)
        self.assertGreater(available, total * self.cfg["low_available_ratio"])
        self.assertLess(available, self.cfg["low_available_bytes"])
        level, _ = policy.classify(snapshot(available, total), self.cfg)
        self.assertEqual(level, "low")

    def test_medium_band(self) -> None:
        level, _ = policy.classify(snapshot(int(8 * GIB * 0.20)), self.cfg)
        self.assertEqual(level, "medium")

    def test_high_band(self) -> None:
        level, _ = policy.classify(snapshot(int(8 * GIB * 0.60)), self.cfg)
        self.assertEqual(level, "high")

    def test_load_downgrades_one_step(self) -> None:
        decision = policy.evaluate(snapshot(int(8 * GIB * 0.60), load1=9.0), self.cfg)
        self.assertEqual(decision.level, "medium")
        self.assertTrue(any("load1" in reason for reason in decision.reasons))

    def test_load_never_upgrades(self) -> None:
        decision = policy.evaluate(snapshot(int(8 * GIB * 0.05), load1=0.0), self.cfg)
        self.assertEqual(decision.level, "low")

    def test_cpu_cap_limits_concurrency(self) -> None:
        cfg = base_config(max_concurrency=64)
        decision = policy.evaluate(snapshot(6 * GIB, cpus=2), cfg)
        self.assertEqual(decision.cpu_cap, 1)
        self.assertEqual(decision.concurrency, 1)

    def test_level_concurrency_mapping(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg)
        self.assertEqual(decision.level, "high")
        self.assertEqual(decision.concurrency, self.cfg["concurrency"]["high"])

    def test_override_level(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, override="low")
        self.assertEqual(decision.level, "low")
        self.assertEqual(decision.concurrency, 1)
        self.assertEqual(decision.override, "low")

    def test_override_integer_string(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, override="2")
        self.assertEqual(decision.concurrency, 2)
        self.assertEqual(decision.override, "2")

    def test_override_clamped_by_max(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, override=99)
        self.assertLessEqual(decision.concurrency, self.cfg["max_concurrency"])
        self.assertEqual(decision.concurrency, decision.cpu_cap)

    def test_zero_override_floors_at_one(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, override=0)
        self.assertGreaterEqual(decision.concurrency, 1)

    def test_pending_limits_concurrency(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, pending=1)
        self.assertEqual(decision.concurrency, 1)

    def test_pending_zero_is_idle(self) -> None:
        decision = policy.evaluate(snapshot(6 * GIB), self.cfg, pending=0)
        self.assertEqual(decision.concurrency, 0)

    def test_budget_never_negative(self) -> None:
        decision = policy.evaluate(snapshot(1), self.cfg)
        self.assertEqual(decision.budget_bytes, 0)

    def test_tracker_records_transitions_only_on_change(self) -> None:
        tracker = policy.Tracker()
        cfg = self.cfg
        tracker.record(policy.evaluate(snapshot(6 * GIB), cfg), 1.0)
        tracker.record(policy.evaluate(snapshot(6 * GIB), cfg), 2.0)
        tracker.record(policy.evaluate(snapshot(int(2 * GIB * 0.05)), cfg), 3.0)
        self.assertEqual(len(tracker.transitions), 2)
        self.assertEqual(tracker.transitions[0]["from"], None)
        self.assertEqual(tracker.transitions[1]["to"], "low")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-ram-cache-"))
        self._env = os.environ.get("PA_RAM_CACHE")
        os.environ["PA_RAM_CACHE"] = str(self.tmp)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("PA_RAM_CACHE", None)
        else:
            os.environ["PA_RAM_CACHE"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get_roundtrip(self) -> None:
        entry = cache.put(b"object-file-bytes")
        self.assertFalse(entry["hit"])
        self.assertEqual(cache.get(entry["key"]), b"object-file-bytes")

    def test_put_is_idempotent(self) -> None:
        first = cache.put(b"same")
        second = cache.put(b"same")
        self.assertTrue(second["hit"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(cache.stats()["entries"], 1)

    def test_corrupt_blob_is_a_miss(self) -> None:
        entry = cache.put(b"payload")
        blob = self.tmp / f"{entry['key']}.blob"
        blob.write_bytes(b"payload-tampered")
        self.assertIsNone(cache.get(entry["key"]))
        self.assertFalse(cache.has(entry["key"]))

    def test_missing_key_is_none(self) -> None:
        self.assertIsNone(cache.get("0" * 64))

    def test_bad_key_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cache.put(b"x", key="not-a-digest")
        self.assertIsNone(cache.get("../etc/passwd"))

    def test_key_of_is_stable_and_separated(self) -> None:
        a = cache.key_of("a", "bc")
        b = cache.key_of("ab", "c")
        self.assertEqual(a, cache.key_of("a", "bc"))
        self.assertNotEqual(a, b)

    def _seed(self, payloads: list[bytes]) -> list[str]:
        keys = [cache.put(payload)["key"] for payload in payloads]
        for offset, key in enumerate(keys):
            stamp = 1_600_000_000 + offset
            for suffix in (".blob", ".json"):
                os.utime(self.tmp / f"{key}{suffix}", (stamp, stamp))
        return keys

    def test_trim_evicts_oldest_first(self) -> None:
        keys = self._seed([b"a" * 100, b"b" * 100, b"c" * 100])
        result = cache.trim(120)
        self.assertEqual(result["evicted"], 2)
        self.assertEqual(result["keys"], keys[:2])
        self.assertIsNone(cache.get(keys[0]))
        self.assertIsNotNone(cache.get(keys[2]))
        self.assertEqual(cache.stats()["bytes"], 100)

    def test_trim_noop_when_under_limit(self) -> None:
        self._seed([b"x" * 10])
        result = cache.trim(10_000)
        self.assertEqual(result["evicted"], 0)
        self.assertEqual(result["freed_bytes"], 0)

    def test_evict_for_reports_success(self) -> None:
        result = cache.evict_for(1024, floor_bytes=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["evicted"], 0)

    def test_evict_for_frees_when_short(self) -> None:
        self._seed([b"z" * 4096])
        needed = cache.free_space(self.tmp) + 1
        result = cache.evict_for(needed, floor_bytes=0)
        self.assertGreaterEqual(result["freed_bytes"], 4096)
        self.assertTrue(result["ok"])
        self.assertEqual(cache.stats()["entries"], 0)
        self.assertGreaterEqual(result["free_after"], needed)

    def test_evict_for_reports_failure_when_impossible(self) -> None:
        self._seed([b"q" * 1024])
        result = cache.evict_for(cache.free_space(self.tmp) + (1 << 40), floor_bytes=0)
        self.assertFalse(result["ok"])
        self.assertIn("could not free", result["note"])

    def test_clear_removes_only_cache_files(self) -> None:
        self._seed([b"one", b"two"])
        outside = self.tmp / "keep.txt"
        outside.write_text("mine", encoding="utf-8")
        removed = cache.clear()
        self.assertGreaterEqual(removed, 4)
        self.assertTrue(outside.is_file())
        self.assertEqual(cache.stats()["entries"], 0)

    def test_clear_on_missing_dir(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(cache.clear(), 0)


class FakeMonitor:
    def __init__(self, points: list[dict]) -> None:
        self.points = points
        self.history: list[dict] = []
        self.calls = 0

    def sample(self) -> dict:
        index = min(self.calls, len(self.points) - 1)
        self.calls += 1
        point = dict(self.points[index])
        point["ts"] = time.time()
        self.history.append(point)
        return point


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-ram-run-"))
        self.sleep = [sys.executable, "-c", "import time; time.sleep(0.15)"]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _job(self, name: str, argv: list[str] | None = None, weight: int = GIB) -> manager.Job:
        return manager.Job(
            id=name,
            argv=tuple(argv or self.sleep),
            cwd=str(self.tmp),
            weight_bytes=weight,
        )

    def test_empty_job_list(self) -> None:
        report = manager.run_jobs([], cfg=base_config())
        self.assertTrue(report.ok)
        self.assertEqual(report.jobs, [])

    def test_single_job_runs(self) -> None:
        report = manager.run_jobs([self._job("one")], cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertTrue(report.ok)
        self.assertEqual(len(report.jobs), 1)
        self.assertEqual(report.jobs[0]["id"], "one")
        self.assertEqual(report.jobs[0]["returncode"], 0)
        self.assertGreaterEqual(report.duration_seconds, 0.1)

    def test_failure_is_reported_not_raised(self) -> None:
        job = self._job("bad", [sys.executable, "-c", "import sys; sys.exit(7)"])
        report = manager.run_jobs([job], cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertFalse(report.ok)
        self.assertEqual(report.jobs[0]["returncode"], 7)
        self.assertFalse(report.jobs[0]["ok"])

    def test_missing_binary_reported(self) -> None:
        job = manager.Job(id="ghost", argv=("pa-binary-that-does-not-exist",))
        report = manager.run_jobs([job], cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertFalse(report.ok)
        self.assertEqual(report.jobs[0]["returncode"], 127)
        self.assertIn("not found", report.jobs[0]["stderr"])

    def test_results_keep_submission_order(self) -> None:
        jobs = [self._job(f"j{index}", [sys.executable, "-c", f"import time; time.sleep({0.1 - index * 0.02})"]) for index in range(3)]
        report = manager.run_jobs(jobs, cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertEqual([job["id"] for job in report.jobs], ["j0", "j1", "j2"])

    def test_duplicate_ids_rejected(self) -> None:
        jobs = [self._job("same"), self._job("same")]
        with self.assertRaises(ValueError):
            manager.run_jobs(jobs, cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))

    def test_parallel_runner_beats_serial(self) -> None:
        jobs = [self._job(f"p{index}") for index in range(4)]
        report = manager.run_jobs(
            jobs,
            cfg=base_config(concurrency={"low": 1, "medium": 2, "high": 3}, max_concurrency=3),
            monitor=FakeMonitor([snapshot(6 * GIB)]),
        )
        self.assertTrue(report.ok)
        self.assertGreater(report.max_inflight, 1)
        self.assertLessEqual(report.max_inflight, 3)
        self.assertLess(report.duration_seconds, 4 * 0.15)

    def test_low_level_forces_sequential(self) -> None:
        jobs = [self._job(f"l{index}") for index in range(3)]
        report = manager.run_jobs(
            jobs,
            cfg=base_config(),
            override="low",
            monitor=FakeMonitor([snapshot(100 * 1024 * 1024)]),
        )
        self.assertEqual(report.max_inflight, 1)
        self.assertTrue(report.ok)
        self.assertEqual(report.final_level, "low")

    def test_level_change_mid_run_is_recorded(self) -> None:
        jobs = [self._job(f"m{index}", [sys.executable, "-c", "import time; time.sleep(0.05)"]) for index in range(2)]
        monitor = FakeMonitor([snapshot(6 * GIB), snapshot(int(8 * GIB * 0.05)), snapshot(6 * GIB)])
        report = manager.run_jobs(jobs, cfg=base_config(), monitor=monitor)
        self.assertTrue(report.ok)
        self.assertGreaterEqual(len(report.transitions), 2)
        self.assertEqual(report.started_level, "high")

    def test_oversized_job_relaxes_gate(self) -> None:
        job = self._job("huge", weight=64 * GIB)
        report = manager.run_jobs([job], cfg=base_config(), monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertTrue(report.ok)
        self.assertEqual(report.relaxed_gates, 1)
        self.assertTrue(any(event.get("relaxed") for event in report.events))

    def test_gate_blocks_when_budget_short(self) -> None:
        jobs = [self._job("a", weight=3 * GIB), self._job("b", weight=3 * GIB)]
        cfg = base_config(concurrency={"low": 1, "medium": 2, "high": 3})
        # budget = 6 GiB - 384 MiB, two 3 GiB jobs cannot share it.
        report = manager.run_jobs(jobs, cfg=cfg, monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertTrue(report.ok)
        self.assertEqual(report.relaxed_gates, 0)
        self.assertTrue(any(event["event"] == "gate" and event.get("blocked") for event in report.events))

    def test_timeout_is_recorded(self) -> None:
        job = self._job("slow", [sys.executable, "-c", "import time; time.sleep(5)"])
        report = manager.run_jobs([job], cfg=base_config(), timeout=1, monitor=FakeMonitor([snapshot(6 * GIB)]))
        self.assertFalse(report.ok)
        self.assertTrue(report.jobs[0]["timed_out"])
        self.assertEqual(report.jobs[0]["returncode"], 124)

    def test_events_include_memory(self) -> None:
        report = manager.run_jobs([self._job("e")], cfg=base_config(), monitor=FakeMonitor([snapshot(5 * GIB)]))
        starts = [event for event in report.events if event["event"] == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["available"], 5 * GIB)

    def test_on_event_callback(self) -> None:
        seen: list[str] = []
        manager.run_jobs(
            [self._job("cb")],
            cfg=base_config(),
            monitor=FakeMonitor([snapshot(6 * GIB)]),
            on_event=lambda event: seen.append(event["event"]),
        )
        self.assertIn("policy", seen)
        self.assertIn("start", seen)
        self.assertIn("finish", seen)

    def test_job_validation(self) -> None:
        with self.assertRaises(ValueError):
            manager.Job(id="empty", argv=())
        with self.assertRaises(ValueError):
            manager.Job(id="zero", argv=("true",), weight_bytes=0)

    def test_manifest_parsing(self) -> None:
        payload = {"jobs": [{"id": "a", "argv": ["true"]}, {"id": "b", "argv": ["true"], "weight_bytes": 1024}]}
        jobs = manager.jobs_from_manifest(payload)
        self.assertEqual([job.id for job in jobs], ["a", "b"])
        self.assertEqual(jobs[1].weight_bytes, 1024)
        self.assertEqual(jobs[0].weight_bytes, policy.DEFAULT_JOB_WEIGHT)

    def test_manifest_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            manager.jobs_from_manifest("nope")
        with self.assertRaises(ValueError):
            manager.jobs_from_manifest({"jobs": ["nope"]})


class ProbeTests(unittest.TestCase):
    def test_probe_returns_series_and_decision(self) -> None:
        result = manager.probe(2, 0.0, override="medium")
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual(result["stats"]["samples"], 2)
        self.assertEqual(result["decision"]["level"], "medium")
        self.assertIn("available", result["series"][0])


if __name__ == "__main__":
    unittest.main()