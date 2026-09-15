#!/usr/bin/env python3
"""Offline smoke for the de-duct-tape refactor: no network, no LLM.

Exercises, against a temp project root:
  1. chapter-count ownership (genre wins, --chapters pre-foundation, no silent clobber)
  2. best-novel tracking (peak recorded, export restores best commit)
  3. foundation checkpoint skip (existing artifacts are not regenerated)
  4. centralized timeouts (named budgets, env overrides, LLM roles)
  5. gen_brief importable errors (FileNotFoundError, not SystemExit)

Run: uv run python scratch/test_refactor_smoke.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths


def _make_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "pyproject.toml").write_text("[tool.gesaku]", encoding="utf-8")
    (root / ".env").write_text("GESAKU_PROVIDER=openai\n", encoding="utf-8")
    return root


class RefactorSmoke(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_refactor_")
        self._saved = {
            "root": paths._root_dir,
            "proj": paths._project_name,
            "env_project": os.environ.get("GESAKU_PROJECT"),
        }
        self.root = _make_root(self._tmp.name)
        paths._root_dir = self.root
        paths._project_name = None
        os.environ.pop("GESAKU_PROJECT", None)

    def tearDown(self):
        paths._root_dir = self._saved["root"]
        paths._project_name = self._saved["proj"]
        if self._saved["env_project"] is None:
            os.environ.pop("GESAKU_PROJECT", None)
        else:
            os.environ["GESAKU_PROJECT"] = self._saved["env_project"]
        self._tmp.cleanup()

    def _bind(self, name: str):
        paths.set_project_name(name)
        paths.get_project_dir().mkdir(parents=True, exist_ok=True)

    def _real_root(self):
        paths._root_dir = Path(__file__).resolve().parent.parent

    # -- 1. chapter-count ownership --------------------------------------

    def test_genre_is_single_owner(self):
        from pipeline import pipeline_infra as infra

        self._bind("smoke_count")
        genre = {
            "generation": {"outline": {"estimated_chapters": 30,
                                       "estimated_words": 90000}},
        }
        paths.get_active_genre_path().write_text(json.dumps(genre), encoding="utf-8")
        state = infra.default_state()
        state["chapters_total"] = 24
        with patch("core.genre.validate", lambda cfg: None):
            total = infra.resolve_chapters_total(state)
        self.assertEqual(total, 30)
        self.assertEqual(state["chapters_total"], 30)

    def test_default_without_genre(self):
        from pipeline import pipeline_infra as infra

        self._bind("smoke_nodefault")
        state = infra.default_state()
        del state["chapters_total"]
        self.assertEqual(infra.resolve_chapters_total(state), infra.CHAPTERS_TOTAL)

    # -- 2. best-novel tracking ------------------------------------------

    def test_record_keeps_peak(self):
        from pipeline import pipeline_infra as infra

        self._bind("smoke_best")
        state = infra.default_state()
        with patch.object(infra, "git_short_hash", return_value="aaa"):
            infra.record_novel_score(state, 7.65, "aaa")
        with patch.object(infra, "git_short_hash", return_value="bbb"):
            infra.record_novel_score(state, 6.86, "bbb")
        self.assertEqual(state["novel_score"], 6.86)
        self.assertEqual(state["best_novel_score"], 7.65)
        self.assertEqual(state["best_novel_commit"], "aaa")
        best, commit = infra.best_novel_checkpoint(state)
        self.assertEqual((best, commit), (7.65, "aaa"))

    def test_record_ignores_unusable(self):
        from pipeline import pipeline_infra as infra

        self._bind("smoke_unusable")
        state = infra.default_state()
        state["novel_score"] = 7.0
        self.assertEqual(infra.record_novel_score(state, 0.0, "zzz"), 7.0)
        self.assertEqual(state["novel_score"], 7.0)

    # -- 3. foundation checkpoint skip -----------------------------------

    def test_checkpoint_helpers(self):
        self._real_root()
        import run_pipeline as rp

        self._bind("smoke_ckpt")
        world = paths.get_world_path()
        world.write_text("x" * 3000, encoding="utf-8")
        self.assertTrue(rp._foundation_artifact_ok(world, min_chars=2000))
        outline = paths.get_outline_path()
        outline.write_text(
            "".join(f"### Chapter {i}: Title number {i} with padding words here\n"
                    + ("body text " * 40 + "\n")
                    for i in range(1, 5)),
            encoding="utf-8",
        )
        self.assertTrue(rp._foundation_artifact_ok(outline, require_chapters=4))
        self.assertFalse(rp._foundation_artifact_ok(outline, require_chapters=5))
        self.assertFalse(rp._foundation_part2_ok(outline, 4))
        outline.write_text(
            outline.read_text(encoding="utf-8") + "\n## FORESHADOWING\n",
            encoding="utf-8",
        )
        self.assertTrue(rp._foundation_part2_ok(outline, 4))

    # -- 4. centralized timeouts -----------------------------------------

    def test_named_budgets_and_env(self):
        self._real_root()
        from pipeline import pipeline_infra as infra
        from core import llm

        self.assertLess(infra.timeout_for("short"), infra.timeout_for("long"))
        self.assertLess(infra.timeout_for("long"), infra.timeout_for("xlong"))
        with patch.dict(os.environ, {"GESAKU_TIMEOUT_SHORT": "11"}):
            self.assertEqual(infra.timeout_for("short"), 11)
        with patch.dict(os.environ, {"GESAKU_LLM_TIMEOUT_XLONG": "22"}):
            self.assertEqual(llm.llm_timeout("xlong"), 22)
        self.assertEqual(llm.llm_timeout("bogus"), llm.LLM_TIMEOUTS["standard"])

    # -- 5. gen_brief raises instead of exiting ----------------------------

    def test_gen_brief_importable_errors(self):
        from pipeline import gen_brief

        self._bind("smoke_brief")
        with self.assertRaises(FileNotFoundError):
            gen_brief.chapter_text(99)


if __name__ == "__main__":
    unittest.main()
