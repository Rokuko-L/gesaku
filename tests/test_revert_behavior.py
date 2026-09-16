#!/usr/bin/env python3
"""Revert behavior — offline.

The phase code reverts a regressed chapter with
`git checkout <best_commit> -- chapters/ch_NN.md`, and unlinks the file when it
was never committed (`git ls-files --error-unmatch` fails). Both branches and
the results.tsv lookup that chooses the commit are exercised here against a
throwaway git repo: no LLM, no network, no dependency on a real project.

(The previous version of this file was a manual script that mutated a real
project's chapter and imported helpers from run_pipeline.py; it never ran in CI
and could not pass on a fresh checkout.)
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths
from pipeline import pipeline_infra as infra


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


class RevertBehaviorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_revert_")
        self._saved_root = paths._root_dir
        self._saved_proj = paths._project_name

        root = Path(self._tmp.name)
        (root / "pyproject.toml").write_text("[tool.gesaku]", encoding="utf-8")
        (root / ".env").write_text("GESAKU_PROVIDER=openai\n", encoding="utf-8")
        paths._root_dir = root
        paths._project_name = None

        paths.set_project_name("revert_probe")
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

    def test_historical_best_reads_results_tsv(self):
        (self.ch_dir / "ch_01.md").write_text("alpha " * 100, encoding="utf-8")
        good = self._commit("keep ch01")

        (self.ch_dir / "ch_01.md").write_text("degraded", encoding="utf-8")
        bad = self._commit("bad ch01")

        infra.log_result(good, "ch01", 7.89, 2870, "keep", "kept")
        infra.log_result(bad, "ch01", 5.10, 90, "discard", "dropped")

        score, commit = infra.get_historical_best_for_chapter(1)
        self.assertEqual(7.89, score)
        self.assertEqual(good, commit)

    def test_checkout_revert_restores_the_best_commit(self):
        original = "alpha " * 200
        (self.ch_dir / "ch_01.md").write_text(original, encoding="utf-8")
        good = self._commit("keep ch01")

        (self.ch_dir / "ch_01.md").write_text("degraded text", encoding="utf-8")
        self._commit("bad ch01")

        infra.run_tool(f"git checkout {good} -- chapters/ch_01.md",
                       cwd=str(self.project))
        self.assertEqual(
            original, (self.ch_dir / "ch_01.md").read_text(encoding="utf-8"))

    def test_uncommitted_chapter_has_no_commit_to_restore(self):
        """The revert branch unlinks instead of checking out when the chapter
        was never tracked — git must report that, or the code would delete a
        file it could have restored."""
        (self.ch_dir / "ch_05.md").write_text("never committed", encoding="utf-8")
        res = infra.run_tool(
            "git ls-files --error-unmatch chapters/ch_05.md", cwd=str(self.project))
        self.assertNotEqual(0, res.returncode)


if __name__ == "__main__":
    unittest.main()
