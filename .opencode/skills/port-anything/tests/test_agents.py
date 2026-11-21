"""Unit and regression tests for the multi-agent module."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from pa.agents import conflict, contract, graph, registry, scheduler, store  # noqa: E402

AGENTS_DIR = SKILL_ROOT.parents[1] / "agents"


_UNSET = object()


def make(
    task_id: str,
    role: str = "cpp-engineer",
    owns: tuple[str, ...] = ("src/engine/core.cpp",),
    outputs: tuple[str, ...] | object = _UNSET,
    depends_on: tuple[str, ...] = (),
    **extra: object,
) -> contract.TaskContract:
    resolved_outputs = (owns[0],) if owns and outputs is _UNSET else outputs
    data = {
        "id": task_id,
        "role": role,
        "objective": f"Implement the {task_id} unit against its stated interface",
        "owns": list(owns),
        "outputs": list(resolved_outputs or ()),
        "done_criteria": [f"the {task_id} tests pass"],
        "depends_on": list(depends_on),
    }
    data.update(extra)
    return contract.TaskContract.from_dict(data)


class ConflictTests(unittest.TestCase):
    def test_normalise(self) -> None:
        self.assertEqual(conflict.normalize("./src//a.cpp"), "src/a.cpp")
        self.assertEqual(conflict.normalize("src\\a.cpp"), "src/a.cpp")

    def test_rejects_escape(self) -> None:
        for bad in ("/etc/passwd", "../outside", "src/../../x", "C:/win", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(conflict.ClaimError):
                    conflict.normalize(bad)

    def test_exact_overlap(self) -> None:
        self.assertTrue(conflict.overlaps("src/a.cpp", "src/a.cpp"))

    def test_directory_claim_covers_child(self) -> None:
        self.assertTrue(conflict.overlaps("src/engine", "src/engine/core.cpp"))

    def test_sibling_directories_are_clean(self) -> None:
        self.assertFalse(conflict.overlaps("src/rust/**", "src/cpp/**"))
        self.assertFalse(conflict.overlaps("src/a.cpp", "src/b.cpp"))

    def test_glob_within_directory(self) -> None:
        self.assertTrue(conflict.overlaps("src/*.rs", "src/main.rs"))
        self.assertFalse(conflict.overlaps("src/*.rs", "src/nested/main.rs"))

    def test_recursive_glob(self) -> None:
        self.assertTrue(conflict.overlaps("src/**/*.rs", "src/a/b/main.rs"))
        self.assertTrue(conflict.overlaps("**/*.rs", "src/main.rs"))

    def test_literal_under_wildcard_root(self) -> None:
        self.assertTrue(conflict.overlaps("src/**", "src/deep/file.bin"))

    def test_two_wildcards_same_root_conservative(self) -> None:
        # Undecidable in general; reported as overlapping on purpose.
        self.assertTrue(conflict.overlaps("src/*.c", "src/*.h"))

    def test_covers(self) -> None:
        claims = ["src/engine/**", "assets/config.json"]
        self.assertTrue(conflict.covers(claims, "src/engine/render/vk.cpp"))
        self.assertTrue(conflict.covers(claims, "assets/config.json"))
        self.assertFalse(conflict.covers(claims, "assets/other.json"))

    def test_find_conflicts_and_sequential(self) -> None:
        owners = [("a", ("src/shared.hpp",)), ("b", ("src/shared.hpp",)), ("c", ("src/other.cpp",))]
        related = lambda first, second: {first, second} == {"a", "b"}  # noqa: E731
        clashes = conflict.find_conflicts(owners, related=related)
        self.assertEqual(len(clashes), 1)
        self.assertTrue(clashes[0].sequential)
        clashes_parallel = conflict.find_conflicts(owners)
        self.assertFalse(clashes_parallel[0].sequential)
        self.assertEqual(clashes_parallel[0].as_dict()["kind"], "parallel-conflict")

    def test_check_dispatch_against_running(self) -> None:
        running = [("a", ("src/engine/**",))]
        clashes = conflict.check_dispatch([("b", ("src/engine/core.cpp",))], running)
        self.assertEqual(len(clashes), 1)
        self.assertEqual(clashes[0].task_b, "a")
        self.assertEqual(conflict.check_dispatch([("b", ("src/other.cpp",))], running), [])


class ContractTests(unittest.TestCase):
    def test_minimal_research_contract(self) -> None:
        task = contract.TaskContract.from_dict(
            {
                "id": "identify-engine",
                "role": "researcher",
                "objective": "Identify the engine and toolchain with evidence",
                "done_criteria": ["engine named with evidence"],
            }
        )
        self.assertEqual(task.owns, ())
        self.assertIn("Identify", task.render_prompt())

    def test_rejects_unknown_role(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", role="orchestrator")

    def test_requires_done_criterion(self) -> None:
        with self.assertRaises(contract.ContractError):
            contract.TaskContract.from_dict(
                {
                    "id": "x",
                    "role": "researcher",
                    "objective": "Identify the engine and toolchain with evidence",
                }
            )

    def test_requires_outputs_to_be_owned(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", owns=("src/a.cpp",), outputs=("src/b.cpp",))

    def test_outputs_need_claims(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", owns=(), outputs=("src/a.cpp",))

    def test_rejects_absolute_claim(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", owns=("/etc/passwd",), outputs=())

    def test_rejects_unnormalised_claim(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", owns=("./src/a.cpp",), outputs=())

    def test_rejects_duplicates(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", owns=("src/a.cpp", "src/a.cpp"))

    def test_rejects_bad_id(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("Bad ID")

    def test_rejects_scope_overlap(self) -> None:
        with self.assertRaises(contract.ContractError):
            make(
                "x",
                scope_in=("src",),
                scope_out=("src",),
            )

    def test_self_dependency(self) -> None:
        with self.assertRaises(contract.ContractError):
            make("x", depends_on=("x",))

    def test_roundtrip(self) -> None:
        task = make("a")
        again = contract.TaskContract.from_dict(task.to_dict())
        self.assertEqual(task, again)

    def test_prompt_has_all_sections(self) -> None:
        task = make("core", depends_on=("identify",))
        text = task.render_prompt(run_id="run-42")
        for needle in (
            "## Objective",
            "## In scope",
            "## Out of scope",
            "## Files you own",
            "## Done when",
            "## Report back",
            "run-42",
            '"task_id": "core"',
        ):
            self.assertIn(needle, text)

    def test_manifest_duplicates_rejected(self) -> None:
        payload = {"tasks": [make("a").to_dict(), make("a").to_dict()]}
        with self.assertRaises(contract.ContractError):
            contract.contracts_from_manifest(payload)

    def test_manifest_rejects_non_object(self) -> None:
        with self.assertRaises(contract.ContractError):
            contract.contracts_from_manifest("nope")


class GraphTests(unittest.TestCase):
    def build(self) -> graph.TaskGraph:
        return graph.TaskGraph.from_contracts(
            [
                make("research", role="researcher", owns=(), outputs=()),
                make("reverse", role="reverse-engineer", owns=("docs/re.md",), outputs=("docs/re.md",), depends_on=("research",)),
                make("rust-core", owns=("src/rust/**",), outputs=("src/rust/lib.rs",), depends_on=("reverse",)),
                make("cpp-core", owns=("src/cpp/**",), outputs=("src/cpp/main.cpp",), depends_on=("reverse",)),
                make(
                    "package",
                    role="android-engineer",
                    owns=("build/**",),
                    outputs=("build/app.apk",),
                    depends_on=("rust-core", "cpp-core"),
                ),
            ]
        )

    def test_topo_order_is_dependency_first(self) -> None:
        order = self.build().topo_order()
        self.assertLess(order.index("research"), order.index("reverse"))
        self.assertLess(order.index("reverse"), order.index("rust-core"))
        self.assertLess(order.index("cpp-core"), order.index("package"))
        self.assertEqual(order[0], "research")

    def test_layers(self) -> None:
        layers = self.build().layers()
        self.assertEqual(layers[0], ["research"])
        self.assertEqual(layers[1], ["reverse"])
        self.assertEqual(layers[2], ["cpp-core", "rust-core"])
        self.assertEqual(layers[3], ["package"])

    def test_ready_respects_done_and_running(self) -> None:
        task_graph = self.build()
        self.assertEqual(task_graph.ready(), ["research"])
        self.assertEqual(task_graph.ready(done={"research"}), ["reverse"])
        self.assertEqual(task_graph.ready(done={"research"}, running={"reverse"}), [])
        self.assertEqual(
            sorted(task_graph.ready(done={"research", "reverse"})),
            ["cpp-core", "rust-core"],
        )

    def test_related_and_downstream(self) -> None:
        task_graph = self.build()
        self.assertTrue(task_graph.related("reverse", "package"))
        self.assertFalse(task_graph.related("rust-core", "cpp-core"))
        self.assertEqual(
            task_graph.downstream("reverse"),
            {"rust-core", "cpp-core", "package"},
        )

    def test_cycle_rejected(self) -> None:
        a = make("a", owns=("src/a.cpp",), depends_on=("b",))
        b = make("b", owns=("src/b.cpp",), depends_on=("a",))
        with self.assertRaises(contract.ContractError):
            graph.TaskGraph.from_contracts([a, b])

    def test_cycle_path_reported(self) -> None:
        a = make("a", owns=("src/a.cpp",), depends_on=("b",))
        b = make("b", owns=("src/b.cpp",), depends_on=("a",))
        loop = graph.TaskGraph(tasks={"a": a, "b": b})
        cycles = loop.cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0][0], cycles[0][-1])

    def test_unknown_dependency(self) -> None:
        with self.assertRaises(contract.ContractError):
            graph.TaskGraph.from_contracts([make("a", depends_on=("ghost",))])

    def test_blocked_by_failed_upstream(self) -> None:
        task_graph = self.build()
        self.assertIn("upstream task failed", task_graph.blocked_by("rust-core", failed={"reverse"}) or "")
        self.assertIn("waiting on", task_graph.blocked_by("rust-core") or "")
        self.assertIsNone(task_graph.blocked_by("research"))

    def test_roots_and_leaves(self) -> None:
        task_graph = self.build()
        self.assertEqual(task_graph.roots(), ["research"])
        self.assertEqual(task_graph.leaves(), ["package"])

    def test_owners_of(self) -> None:
        owners = dict(graph.owners_of(self.build()))
        self.assertNotIn("research", owners)
        self.assertEqual(owners["cpp-core"], ("src/cpp/**",))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-agents-"))
        self.path = self.tmp / "state.json"
        self.graph = graph.TaskGraph.from_contracts(
            [
                make("a", owns=("src/a.cpp",)),
                make("b", owns=("src/b.cpp",), depends_on=("a",)),
            ]
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_reload(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.save()
        reloaded = store.RunStore.load(self.path, self.graph)
        self.assertEqual(reloaded.counts()["pending"], 2)
        self.assertEqual(reloaded.data["run"], run.data["run"])

    def test_illegal_transition(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        with self.assertRaises(store.StateError):
            run.finish("a")
        run.start("a")
        run.finish("a")
        with self.assertRaises(store.StateError):
            run.start("a")

    def test_start_finish_and_attempts(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        self.assertEqual(run.status("a"), "running")
        self.assertEqual(run.task("a")["attempts"], 1)
        run.finish("a", result={"ok": True})
        self.assertEqual(run.status("a"), "done")
        self.assertIsNotNone(run.task("a")["finished"])

    def test_failure_and_retry(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.finish("a", ok=False, result={"error": "compile failed"})
        self.assertEqual(run.status("a"), "failed")
        self.assertIn("compile failed", run.task("a")["error"])
        run.retry("a")
        self.assertEqual(run.status("a"), "pending")
        run.start("a")
        self.assertEqual(run.task("a")["attempts"], 2)

    def test_claim_conflict_between_live_tasks(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.claim("a", ("src/**",))
        run.start("b")
        with self.assertRaises(store.ClaimConflict):
            run.claim("b", ("src/a.cpp",))
        run.finish("a")
        run.claim("b", ("src/a.cpp",))

    def test_claim_force(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.claim("a", ("src/**",))
        run.start("b")
        run.claim("b", ("src/**",), force=True)
        self.assertEqual(run.claims()["src/**"], "b")

    def test_release(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.claim("a", ("src/a.cpp",))
        run.release("a")
        self.assertEqual(run.claims(), {})

    def test_release_stale(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a", now=1000.0)
        run.claim("a", ("src/a.cpp",))
        released = run.release_stale(ttl_seconds=60, now=1100.0)
        self.assertEqual(released, ["a"])
        self.assertEqual(run.status("a"), "failed")
        self.assertEqual(run.claims(), {})
        self.assertIn("stale", run.task("a")["error"])

    def test_fresh_running_task_not_released(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a", now=1000.0)
        self.assertEqual(run.release_stale(ttl_seconds=60, now=1010.0), [])

    def test_blocked_and_skipped(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.block("a", "missing NDK")
        self.assertEqual(run.status("a"), "blocked")
        run.skip("b", "not needed")
        self.assertEqual(run.status("b"), "skipped")
        self.assertTrue(run.finished())

    def test_done_ids_and_counts(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.finish("a")
        self.assertEqual(run.done_ids(), {"a"})
        self.assertEqual(run.counts()["done"], 1)
        self.assertFalse(run.finished())

    def test_unknown_task(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        with self.assertRaises(store.StateError):
            run.status("ghost")

    def test_load_rejects_unknown_task(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.data["tasks"]["ghost"] = {"status": "pending"}
        run.save()
        with self.assertRaises(store.StateError):
            store.RunStore.load(self.path, self.graph)

    def test_load_rejects_bad_version(self) -> None:
        self.path.write_text(json.dumps({"version": 99, "tasks": {}}), encoding="utf-8")
        with self.assertRaises(store.StateError):
            store.RunStore.load(self.path)

    def test_load_rejects_bad_status(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.data["tasks"]["a"]["status"] = "exploded"
        run.save()
        with self.assertRaises(store.StateError):
            store.RunStore.load(self.path)

    def test_resume_adds_new_tasks(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("a")
        run.finish("a")
        run.save()
        bigger = graph.TaskGraph.from_contracts(
            [
                make("a", owns=("src/a.cpp",)),
                make("b", owns=("src/b.cpp",), depends_on=("a",)),
                make("c", owns=("src/c.cpp",), depends_on=("b",)),
            ]
        )
        resumed = store.RunStore.create(bigger, self.path)
        self.assertEqual(resumed.status("a"), "done")
        self.assertEqual(resumed.status("c"), "pending")

    def test_atomic_save_leaves_no_temp(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.save()
        self.assertEqual(list(self.tmp.glob(".state-*")), [])


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-sched-"))
        self.path = self.tmp / "state.json"
        self.graph = graph.TaskGraph.from_contracts(
            [
                make("research", role="researcher", owns=(), outputs=()),
                make("rust", owns=("src/rust/**",), outputs=("src/rust/lib.rs",), depends_on=("research",)),
                make("cpp", owns=("src/cpp/**",), outputs=("src/cpp/main.cpp",), depends_on=("research",)),
                make("package", role="android-engineer", owns=("build/**",), outputs=("build/app.apk",), depends_on=("rust", "cpp")),
            ]
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clamp(self) -> None:
        self.assertEqual(scheduler.clamp_concurrency(0), 1)
        self.assertEqual(scheduler.clamp_concurrency(2), 2)
        self.assertEqual(scheduler.clamp_concurrency(99), 3)
        with self.assertRaises(ValueError):
            scheduler.clamp_concurrency(True)  # type: ignore[arg-type]

    def test_plan_starts_with_roots(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        plan_out = scheduler.plan(self.graph, run, concurrency=2)
        self.assertEqual(plan_out.batch, ["research"])
        self.assertIn("waiting on", plan_out.deferred["rust"])

    def test_plan_respects_concurrency(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("research")
        run.finish("research")
        plan_out = scheduler.plan(self.graph, run, concurrency=1)
        self.assertEqual(len(plan_out.batch), 1)
        deferred = [task_id for task_id, reason in plan_out.deferred.items() if "concurrency" in reason]
        self.assertEqual(len(deferred), 1)
        plan_out = scheduler.plan(self.graph, run, concurrency=2)
        self.assertEqual(plan_out.batch, ["cpp", "rust"])

    def test_plan_defers_file_conflict(self) -> None:
        clashing = graph.TaskGraph.from_contracts(
            [
                make("a", owns=("src/shared.hpp",)),
                make("b", owns=("src/shared.hpp",)),
            ]
        )
        run = store.RunStore.create(clashing, self.path)
        plan_out = scheduler.plan(clashing, run, concurrency=2)
        self.assertEqual(len(plan_out.batch), 1)
        self.assertTrue(any("file conflict" in reason for reason in plan_out.deferred.values()))

    def test_plan_allows_disjoint_claims_while_running(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("research")
        run.finish("research")
        run.start("rust")
        run.claim("rust", ("src/rust/**",))
        plan_out = scheduler.plan(self.graph, run, concurrency=2)
        self.assertNotIn("rust", plan_out.ready)
        self.assertIn("cpp", plan_out.batch)

    def test_plan_defers_overlapping_running_claim(self) -> None:
        clashing = graph.TaskGraph.from_contracts(
            [
                make("holder", owns=("src/shared/**",)),
                make("waiter", owns=("src/shared/core.cpp",)),
            ]
        )
        run = store.RunStore.create(clashing, self.path)
        run.start("holder")
        run.claim("holder", ("src/shared/**",))
        plan_out = scheduler.plan(clashing, run, concurrency=2)
        self.assertEqual(plan_out.batch, [])
        self.assertIn("file conflict with holder", plan_out.deferred["waiter"])

    def test_mark_dispatched_starts_and_claims(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        plan_out = scheduler.plan(self.graph, run, concurrency=2)
        scheduler.mark_dispatched(run, plan_out.batch)
        self.assertEqual(run.status("research"), "running")
        self.assertEqual(run.claims(), {})

    def test_simulate_covers_every_task(self) -> None:
        batches = scheduler.simulate(self.graph, concurrency=2)
        seen = [task_id for batch in batches for task_id in batch]
        self.assertEqual(sorted(seen), self.graph.ids())
        self.assertEqual(batches[0], ["research"])
        self.assertEqual(batches[-1], ["package"])

    def test_simulate_serialises_conflicting_tasks(self) -> None:
        clashing = graph.TaskGraph.from_contracts(
            [
                make("a", owns=("src/**",)),
                make("b", owns=("src/main.cpp",)),
                make("c", owns=("src/other.cpp",)),
            ]
        )
        batches = scheduler.simulate(clashing, concurrency=3)
        # "a" claims all of src, so it runs alone; b and c are disjoint and share
        # the next wave.
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0], ["a"])
        self.assertEqual(batches[1], ["b", "c"])

    def test_pending_owners_skips_done(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        run.start("research")
        run.finish("research")
        owners = dict(scheduler.pending_owners(self.graph, run))
        self.assertNotIn("research", owners)
        self.assertIn("package", owners)

    def test_describe(self) -> None:
        run = store.RunStore.create(self.graph, self.path)
        text = scheduler.describe(scheduler.plan(self.graph, run, concurrency=2))
        self.assertIn("dispatch 1", text)
        self.assertIn("deferred", text)


class RegistryTests(unittest.TestCase):
    def test_repository_agents_are_valid(self) -> None:
        result = registry.check(AGENTS_DIR)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(len(result["found"]), 9)

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.check(tmp)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["missing"]), 9)

    def test_frontmatter_mismatch_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(AGENTS_DIR, Path(tmp) / "agents")
            target = Path(tmp) / "agents" / "researcher.md"
            text = target.read_text(encoding="utf-8").replace("name: researcher", "name: someone-else")
            target.write_text(text, encoding="utf-8")
            result = registry.check(Path(tmp) / "agents")
        self.assertFalse(result["ok"])
        self.assertTrue(any("does not match filename" in problem for problem in result["problems"]))

    def test_extra_agent_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(AGENTS_DIR, Path(tmp) / "agents")
            (Path(tmp) / "agents" / "sidekick.md").write_text("---\nname: sidekick\n---\n", encoding="utf-8")
            result = registry.check(Path(tmp) / "agents")
        self.assertIn("sidekick", result["extra"])


if __name__ == "__main__":
    unittest.main()