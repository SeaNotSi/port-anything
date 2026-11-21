"""Tests for the installer: discovery, planning, idempotency, uninstall, check."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from pa import install  # noqa: E402


def make_source_tree(root: Path) -> tuple[Path, Path]:
    skill = root / "skill"
    agents = root / "agents"
    (skill / "src" / "pa").mkdir(parents=True)
    (skill / "config").mkdir(parents=True)
    (skill / "templates").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (skill / "src" / "pa" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (skill / "config" / "ram.json").write_text("{}\n", encoding="utf-8")
    (skill / "templates" / "opencode.project.jsonc").write_text("{\"instructions\": []}\n", encoding="utf-8")
    # Ignored content.
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    (skill / "src" / "pa" / "stale.pyc").write_bytes(b"\x00")
    agents.mkdir()
    (agents / "researcher.md").write_text("# researcher\n", encoding="utf-8")
    (agents / "optimizer.md").write_text("# optimizer\n", encoding="utf-8")
    (agents / "notes.txt").write_text("ignored\n", encoding="utf-8")
    return skill, agents


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa-install-"))
        self.skill, self.agents = make_source_tree(self.tmp)
        self.project = self.tmp / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_kwargs(self) -> dict:
        return {"skill_root": self.skill, "agents_dir": self.agents}

    # --------------------------------------------------------------- discovery

    def test_collect_sources_maps_layout(self) -> None:
        sources = install.collect_sources(self.skill, self.agents)
        self.assertIn("skills/port-anything/SKILL.md", sources)
        self.assertIn("skills/port-anything/src/pa/__init__.py", sources)
        self.assertIn("agents/researcher.md", sources)
        self.assertIn("agents/optimizer.md", sources)

    def test_collect_sources_ignores_caches_and_non_markdown(self) -> None:
        sources = install.collect_sources(self.skill, self.agents)
        joined = " ".join(sources)
        self.assertNotIn("__pycache__", joined)
        self.assertNotIn(".pyc", joined)
        self.assertNotIn("notes.txt", joined)

    def test_target_root_project_and_global(self) -> None:
        project_target = install.target_root("project", self.project)
        self.assertEqual(project_target.name, ".opencode")
        self.assertEqual(project_target.parent, self.project)
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")
        try:
            global_target = install.target_root("global")
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous
        self.assertEqual(global_target, self.tmp / "cfg" / "opencode")

    def test_unknown_scope_raises(self) -> None:
        with self.assertRaises(ValueError):
            install.target_root("sideways")

    # ----------------------------------------------------------------- install

    def test_install_then_check_is_current(self) -> None:
        report = install.install("project", self.project, **self.install_kwargs())
        self.assertTrue(report.ok(), report.messages)
        target = self.project / ".opencode"
        self.assertTrue((target / "skills" / "port-anything" / "SKILL.md").is_file())
        self.assertTrue((target / "agents" / "researcher.md").is_file())
        self.assertTrue((target / install.MANIFEST_NAME).is_file())
        # A project install seeds opencode.jsonc from the template.
        self.assertTrue((self.project / "opencode.jsonc").is_file())

        verified = install.check("project", self.project, **self.install_kwargs())
        self.assertTrue(verified.ok())
        self.assertEqual(verified.actions, [])

    def test_install_is_idempotent(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        second = install.install("project", self.project, **self.install_kwargs())
        self.assertEqual(second.counts().get("create", 0), 0)
        self.assertEqual(second.counts().get("update", 0), 0)
        self.assertGreater(second.counts().get("unchanged", 0), 0)

    def test_install_preserves_existing_config(self) -> None:
        config = self.project / "opencode.jsonc"
        config.write_text('{"model": "chosen/by/the/user"}\n', encoding="utf-8")
        install.install("project", self.project, **self.install_kwargs())
        self.assertEqual(config.read_text(encoding="utf-8"), '{"model": "chosen/by/the/user"}\n')

    def test_dry_run_writes_nothing(self) -> None:
        report = install.install("project", self.project, dry_run=True, **self.install_kwargs())
        self.assertTrue(report.dry_run)
        self.assertFalse((self.project / ".opencode").exists())

    def test_check_detects_drift(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        edited = self.project / ".opencode" / "agents" / "researcher.md"
        edited.write_text("# edited by hand\n", encoding="utf-8")
        report = install.check("project", self.project, **self.install_kwargs())
        self.assertFalse(report.ok() and not report.actions)
        rels = {action.relative for action in report.actions}
        self.assertIn("agents/researcher.md", rels)

    def test_check_reports_missing_install(self) -> None:
        report = install.check("project", self.project, **self.install_kwargs())
        self.assertFalse(report.ok())

    def test_stale_file_removed_on_reinstall(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        stale = self.skill / "src" / "pa" / "extra.py"
        stale.write_text("y = 2\n", encoding="utf-8")
        install.install("project", self.project, **self.install_kwargs())
        installed = self.project / ".opencode" / "skills" / "port-anything" / "src" / "pa" / "extra.py"
        self.assertTrue(installed.is_file())
        stale.unlink()
        install.install("project", self.project, **self.install_kwargs())
        self.assertFalse(installed.is_file())

    # --------------------------------------------------------------- uninstall

    def test_uninstall_removes_tracked_files_only(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        target = self.project / ".opencode"
        keep = target / "keepme.txt"
        keep.write_text("mine\n", encoding="utf-8")
        report = install.uninstall("project", self.project)
        self.assertTrue(report.ok())
        self.assertFalse((target / "skills" / "port-anything" / "SKILL.md").is_file())
        self.assertTrue(keep.is_file())
        self.assertTrue((self.project / "opencode.jsonc").is_file())

    def test_uninstall_without_manifest_is_a_noop(self) -> None:
        report = install.uninstall("project", self.project)
        self.assertTrue(report.ok())
        self.assertEqual(report.actions, [])

    def test_uninstall_dry_run_keeps_files(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        install.uninstall("project", self.project, dry_run=True)
        self.assertTrue((self.project / ".opencode" / "skills" / "port-anything" / "SKILL.md").is_file())

    def test_edited_file_not_removed_by_uninstall(self) -> None:
        install.install("project", self.project, **self.install_kwargs())
        edited = self.project / ".opencode" / "agents" / "researcher.md"
        edited.write_text("# hand edited after install\n", encoding="utf-8")
        report = install.uninstall("project", self.project)
        states = {action.relative: action.state for action in report.actions}
        self.assertEqual(states.get("agents/researcher.md"), "unchanged")
        self.assertTrue(edited.is_file())


if __name__ == "__main__":
    unittest.main()
