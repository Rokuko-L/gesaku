#!/usr/bin/env python3
"""Export ships the PEAK — the restore path, offline.

`run_export` restores the best-scoring commit's chapters before building
deliverables. The previous suite only covered the score *tracking* helper, so
the restore branch (and the two ways it can ship the wrong thing) had no
coverage at all.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths
from pipeline.phases import export as export_mod


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


class ExportRestoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_export_")
        self._saved_root = paths._root_dir
        self._saved_proj = paths._project_name

        root = Path(self._tmp.name)
        (root / "pyproject.toml").write_text("[tool.gesaku]", encoding="utf-8")
        (root / ".env").write_text("GESAKU_PROVIDER=openai\n", encoding="utf-8")
        paths._root_dir = root
        paths._project_name = None

        paths.set_project_name("export_probe")
        self.project = paths.get_project_dir()
        self.project.mkdir(parents=True, exist_ok=True)
        self.ch_dir = paths.get_chapters_dir()
        self.ch_dir.mkdir(parents=True, exist_ok=True)

        _git(["init", "-q", "."], self.project)
        _git(["config", "user.email", "t@t"], self.project)
        _git(["config", "user.name", "t"], self.project)

    def tearDown(self):
        paths._root_dir = self._saved_root
        paths._project_name = self._saved_proj
        self._tmp.cleanup()

    def _commit(self, message: str) -> str:
        _git(["add", "-A"], self.project)
        _git(["commit", "-q", "-m", message], self.project)
        return _git(["rev-parse", "--short", "HEAD"], self.project).stdout.strip()

    def test_restore_mirrors_the_peak_and_drops_later_chapters(self):
        """`git checkout <c> -- chapters` alone leaves chapters that were added
        after the peak, so the export would ship peak text plus later work."""
        (self.ch_dir / "ch_01.md").write_text("peak one", encoding="utf-8")
        (self.ch_dir / "ch_02.md").write_text("peak two", encoding="utf-8")
        peak = self._commit("peak")

        (self.ch_dir / "ch_01.md").write_text("regressed one", encoding="utf-8")
        (self.ch_dir / "ch_03.md").write_text("added after peak", encoding="utf-8")
        self._commit("later, worse")

        self.assertTrue(export_mod._restore_best_novel(peak))
        self.assertEqual("peak one",
                         (self.ch_dir / "ch_01.md").read_text(encoding="utf-8"))
        self.assertEqual("peak two",
                         (self.ch_dir / "ch_02.md").read_text(encoding="utf-8"))
        self.assertFalse((self.ch_dir / "ch_03.md").exists(),
                         "a chapter absent from the peak must not ship with it")

    def test_restore_brings_back_the_plant_store(self):
        store = paths.get_open_callbacks_path()
        store.write_text(json.dumps({"callbacks": ["peak store"]}), encoding="utf-8")
        (self.ch_dir / "ch_01.md").write_text("peak one", encoding="utf-8")
        peak = self._commit("peak")

        store.write_text(json.dumps({"callbacks": ["later store"]}), encoding="utf-8")
        self._commit("later")

        self.assertTrue(export_mod._restore_best_novel(peak))
        self.assertIn("peak store", store.read_text(encoding="utf-8"))

    def test_restore_drops_a_store_the_peak_never_had(self):
        """A store describing prose that is no longer on disk is worse than no
        store at all."""
        (self.ch_dir / "ch_01.md").write_text("peak one", encoding="utf-8")
        peak = self._commit("peak")

        store = paths.get_open_callbacks_path()
        store.write_text(json.dumps({"callbacks": ["orphan"]}), encoding="utf-8")

        self.assertTrue(export_mod._restore_best_novel(peak))
        self.assertFalse(store.exists())

    def test_restore_reports_failure_for_an_unknown_commit(self):
        self.assertFalse(export_mod._restore_best_novel("deadbeef"))


if __name__ == "__main__":
    unittest.main()
