#!/usr/bin/env python3
"""Offline smoke for the de-duct-tape refactor: no network, no LLM.

Exercises, against a temp project root:
  1. chapter-count ownership (genre wins, --chapters pre-foundation, no silent clobber)
  2. best-novel tracking (peak recorded, export restores best commit)
  3. foundation checkpoint skip (existing artifacts are not regenerated)
  4. centralized timeouts (named budgets, env overrides, LLM roles)
  5. gen_brief importable errors (FileNotFoundError, not SystemExit)

Run: uv run python tests/test_refactor_smoke.py
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
from pipeline.pipeline_infra import timeout_for


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
        # The smoke project persists under the real projects/ dir, so clear any
        # marker a previous run left behind before asserting on its absence.
        paths.get_outline_part2_path().unlink(missing_ok=True)
        self.assertFalse(rp._foundation_part2_ok(outline, 4))
        # Part-2 completion is an explicit marker, not a prose guess.
        paths.get_outline_part2_path().write_text("done\n", encoding="utf-8")
        self.assertTrue(rp._foundation_part2_ok(outline, 4))
        # A regenerated part-1 outline clears the marker (gen_outline does
        # this) — the checkpoint must not report done for unseen prose.
        paths.get_outline_part2_path().unlink()
        self.assertFalse(rp._foundation_part2_ok(outline, 4))
        # Marker alone is not enough: the tail chapters must still be present.
        paths.get_outline_part2_path().write_text("done\n", encoding="utf-8")
        outline.write_text(
            "".join(f"### Chapter {i}: Title number {i}\n" + ("body " * 40 + "\n")
                    for i in range(1, 3)),
            encoding="utf-8",
        )
        self.assertFalse(rp._foundation_part2_ok(outline, 4))
        paths.get_outline_part2_path().unlink(missing_ok=True)

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
        self._real_root()
        from pipeline.briefs import context as brief_context

        self._bind("smoke_brief")
        with self.assertRaises(FileNotFoundError):
            brief_context.chapter_text(99)

    # -- 6. tolerances are one named policy --------------------------------

    def test_tolerance_helpers(self):
        self._real_root()
        from pipeline import pipeline_infra as infra

        self.assertGreater(infra.revision_tolerance(), infra.cuts_tolerance())
        self.assertGreater(infra.force_keep_margin(), infra.near_clean_margin())
        with patch.dict(os.environ, {"GESAKU_CUTS_TOLERANCE": "0.5"}):
            self.assertEqual(infra.cuts_tolerance(), 0.5)
        with patch.dict(os.environ, {"GESAKU_DECLINE_STREAK": "3"}):
            self.assertEqual(infra.decline_streak(), 3)

    def test_genre_cache_is_per_project(self):
        """Set a genre for project A, switch to B, and B must not see A's."""
        from core import genre

        self._bind("cache_a")
        paths.get_active_genre_path().write_text(
            json.dumps({"genre_name": "A", "generation": {
                "outline": {"estimated_chapters": 30, "estimated_words": 90000}}}),
            encoding="utf-8",
        )
        with patch("core.genre.validate", lambda cfg: None):
            self.assertEqual(genre.chapters_total(), 30)

        self._bind("cache_b")
        # B has no genre file -> must not inherit A's cached config.
        self.assertIsNone(genre.chapters_total())

    # -- 11. outline subprocess cap covers its own retry budget -----------

    def test_outline_cap_covers_retry_budget(self):
        """A flat cap can expire mid-retry even when every LLM call fit its
        own budget: roadmap attempts + block attempts, each llm_timeout(long)."""
        from pipeline.phases import foundation as fnd
        from core.llm import llm_timeout

        self._bind("smoke_cap")
        state = {"chapters_total": 24}
        with patch.dict(os.environ, {}, clear=False):
            cap = fnd._outline_subprocess_cap(state)
        # Worst case is the roadmap alone: 6 attempts x long budget.
        self.assertGreaterEqual(cap, 6 * llm_timeout("long"))
        # And it must stay at or above the plain backstop.
        self.assertGreaterEqual(cap, timeout_for("xlong"))

    def test_outline_cap_scales_with_block_count(self):
        from pipeline.phases import foundation as fnd
        from core.llm import llm_timeout

        self._bind("smoke_cap2")
        small = fnd._outline_subprocess_cap({"chapters_total": 4})
        big = fnd._outline_subprocess_cap({"chapters_total": 60})
        self.assertGreater(big, small)
        self.assertGreaterEqual(big, 6 * llm_timeout("long"))

    # -- 12. callbacks ride with their chapter ----------------------------

    def test_stage_chapter_with_callbacks_stages_both(self):
        """The plant store is chapter-scoped: staging the prose without it
        leaves a revert pairing best-chapters with callbacks quoted from
        text that no longer exists."""
        self._real_root()
        import subprocess
        from pipeline.phases.common import stage_chapter_with_callbacks

        self._bind("smoke_stage")
        project = paths.get_project_dir()
        subprocess.run(["git", "init", "-q", str(project)], capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project,
                       capture_output=True)

        ch = paths.get_chapters_dir() / "ch_03.md"
        ch.write_text("# Chapter 3\n\nprose\n", encoding="utf-8")
        cb = paths.get_open_callbacks_path()
        cb.write_text('{"callbacks": []}', encoding="utf-8")

        stage_chapter_with_callbacks(3)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=project, capture_output=True, text=True).stdout
        self.assertIn("chapters/ch_03.md", staged)
        self.assertIn("open_callbacks.json", staged,
                      "callbacks must be staged with the chapter they describe")

    def test_stage_survives_missing_callbacks_file(self):
        """First extraction has not run yet — staging must not fail."""
        self._real_root()
        import subprocess
        from pipeline.phases.common import stage_chapter_with_callbacks

        self._bind("smoke_stage2")
        project = paths.get_project_dir()
        subprocess.run(["git", "init", "-q", str(project)], capture_output=True)

        (paths.get_chapters_dir() / "ch_04.md").write_text("x\n", encoding="utf-8")
        self.assertFalse(paths.get_open_callbacks_path().exists())
        stage_chapter_with_callbacks(4)  # must not raise
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=project, capture_output=True, text=True).stdout
        self.assertIn("chapters/ch_04.md", staged)

    def test_git_clean_spares_every_artifact_a_later_stage_reads(self):
        """Behavioral: run the exact `git clean` command git_reset_hard builds
        and assert each artifact the pipeline reads back survives.

        The list of names here is deliberately independent of CLEAN_KEEP — a
        test that iterated CLEAN_KEEP would pass even with an entry missing
        from it, which is precisely how `.outline_part2.done` shipped
        unprotected while the docs claimed otherwise.
        """
        self._real_root()
        import subprocess
        from pipeline import pipeline_infra as infra

        must_survive = [
            "open_callbacks.json", ".outline_roadmap.md", ".outline_part1.md",
            ".outline_part2.done", "premise_validation.json",
            "plant_hygiene.json", "reviews.md", "run.json",
            "retry_feedback_ch03.txt", "repetition_check.json",
        ]
        dirs = ["eval_logs", "edit_logs", "briefs", "logs"]

        with tempfile.TemporaryDirectory(prefix="gesaku_clean_") as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
            for name in must_survive:
                (repo / name).write_text("x", encoding="utf-8")
            for d in dirs:
                (repo / d).mkdir()
                (repo / d / "keep.txt").write_text("x", encoding="utf-8")
            (repo / "chapters").mkdir()
            (repo / "chapters" / "ch_01.md").write_text("x", encoding="utf-8")

            excludes = " ".join(f"-e {name}" for name in infra.CLEAN_KEEP)
            infra.run_tool(f"git clean -fd {excludes}", cwd=str(repo))

            for name in must_survive:
                self.assertTrue((repo / name).exists(),
                                f"git_reset_hard's clean would delete {name}")
            for d in dirs:
                self.assertTrue((repo / d).exists(),
                                f"git_reset_hard's clean would delete {d}/")
            self.assertFalse(
                (repo / "chapters").exists(),
                "an unexcluded untracked path must still be cleaned, or the "
                "reset stops doing its job")

    def test_revert_paths_do_not_stage_the_store(self):
        """A revert has no commit. Staging the store there leaves it in the
        index, where a later `git reset --hard` discards it (restoring a
        stale copy) or a later chapter's `git_commit_staged` sweeps it in
        under the wrong message. Re-extract into the working tree only."""
        self._real_root()
        import inspect
        from pipeline.phases import review_loop, revision

        for mod, name in ((revision, "run_revision"),
                          (review_loop, "run_opus_review_loop")):
            src = inspect.getsource(getattr(mod, name))
            self.assertIn("stage_chapter_with_callbacks", src,
                          f"{name} must still stage on the KEEP path")
            revert_sites = [ln for ln in src.splitlines()
                            if "revert" in ln.lower()]
            self.assertTrue(revert_sites, f"{name} has no revert branch?")
            # Structural guard: the helper is only ever called immediately
            # before git_commit_staged, never after a bare log_result("reverted").
            lines = src.splitlines()
            for i, ln in enumerate(lines):
                if "stage_chapter_with_callbacks" not in ln:
                    continue
                window = "\n".join(lines[i:i + 6])
                self.assertIn(
                    "git_commit_staged", window,
                    f"{name}:{i + 1} stages the store without an immediate "
                    f"commit — unsafe on a revert path")

    def test_no_staging_inside_a_revert_branch(self):
        """Guard the invariant, not just the helper name.

        The check above only inspects calls to `stage_chapter_with_callbacks`;
        a raw `run_tool("git add ...")` reintroduced on a revert branch would
        pass it while still stranding the index. Scan each revert branch
        (from the "reverting" marker to its `log_result("reverted"`) for any
        staging at all.
        """
        self._real_root()
        import inspect
        from pipeline.phases import review_loop, revision

        for mod, name in ((revision, "run_revision"),
                          (review_loop, "run_opus_review_loop")):
            lines = inspect.getsource(getattr(mod, name)).splitlines()
            in_revert = False
            saw_revert = False
            for i, ln in enumerate(lines):
                low = ln.lower()
                if "reverting" in low:
                    in_revert = True
                    saw_revert = True
                if 'log_result("reverted"' in ln:
                    in_revert = False
                    continue
                if in_revert and ("git add" in low
                                  or "stage_chapter_with_callbacks" in ln):
                    self.fail(
                        f"{name}:{i + 1} stages inside a revert branch — there "
                        f"is no commit there, so the index is left dirty and a "
                        f"later git_commit_staged sweeps it under the wrong "
                        f"message:\n    {ln.strip()}")
            self.assertTrue(saw_revert, f"{name} has no revert branch?")

    def test_revert_leaves_index_clean(self):
        """End-to-end shape of the bug: re-extract after a `git checkout`
        must leave the index untouched, so a later reset cannot resurrect a
        stale store and a later keep cannot steal the change."""
        self._real_root()
        import subprocess
        from pipeline.phases.common import stage_chapter_with_callbacks

        self._bind("smoke_revert_clean")
        project = paths.get_project_dir()
        subprocess.run(["git", "init", "-q", str(project)], capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project,
                       capture_output=True)

        ch = paths.get_chapters_dir() / "ch_05.md"
        ch.write_text("# Chapter 5\n\nv1 prose\n", encoding="utf-8")
        cb = paths.get_open_callbacks_path()
        cb.write_text('{"callbacks": [{"id": "a", "text": "v1 brush"}]}',
                      encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "c1"], cwd=project,
                       capture_output=True)

        # Simulate a rejected revision: prose rewritten, store re-extracted
        # from the rewrite, then reverted to the committed chapter.
        ch.write_text("# Chapter 5\n\nv2 prose that will be rejected\n",
                      encoding="utf-8")
        cb.write_text('{"callbacks": [{"id": "b", "text": "v2 spoon"}]}',
                      encoding="utf-8")
        subprocess.run(["git", "checkout", "HEAD", "--",
                        "chapters/ch_05.md"], cwd=project,
                       capture_output=True)
        # Revert path re-extracts from the restored prose; it does NOT stage.
        cb.write_text('{"callbacks": [{"id": "a", "text": "v1 brush"}]}',
                      encoding="utf-8")

        status = subprocess.run(["git", "status", "--porcelain"],
                                cwd=project, capture_output=True,
                                text=True).stdout
        self.assertNotIn("M  open_callbacks.json", status,
                         "revert must not stage the store")
        self.assertNotIn("M open_callbacks.json", status,
                         "revert must not leave the store as a staged change")
        # Sanity: the KEEP helper does stage, so the guard above is meaningful.
        ch.write_text("# Chapter 5\n\nv3 kept\n", encoding="utf-8")
        stage_chapter_with_callbacks(5)
        status = subprocess.run(["git", "status", "--porcelain"],
                                cwd=project, capture_output=True,
                                text=True).stdout
        self.assertIn("chapters/ch_05.md", status)

    # -- 7. drift verdict cannot silently default to "clean" ---------------

    def test_tonal_drift_verdict_requires_has_drift(self):
        self._real_root()
        from core.validation import (
            OutputValidationError, TonalDriftVerdict, parse_validated,
        )

        ok = parse_validated(
            TonalDriftVerdict, '{"has_drift": "true", "violations": "one"}',
            context="t")
        self.assertIs(ok.has_drift, True)
        self.assertEqual(ok.violations, ["one"])

        with self.assertRaises(OutputValidationError):
            parse_validated(TonalDriftVerdict, '{"analysis": "looks fine"}',
                            context="t")

    # -- 8. run_tool honors check=True on timeout --------------------------

    def test_run_tool_timeout_semantics(self):
        self._real_root()
        from pipeline import pipeline_infra as infra

        slow = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        with self.assertRaises(Exception):
            infra.run_tool(slow, timeout=1, check=True)
        res = infra.run_tool(slow, timeout=1, check=False)
        self.assertEqual(res.returncode, -1)
        self.assertEqual(res.stderr, "TIMEOUT")

    # -- 9. every static prompt loads and its placeholders resolve ---------

    def test_prompts_load_and_format(self):
        self._real_root()
        prompts_dir = self.root / "prompts"
        if not prompts_dir.is_dir():
            prompts_dir = Path(__file__).resolve().parent.parent / "prompts"

        files = sorted(prompts_dir.glob("*.md"))
        self.assertGreater(len(files), 10)

        for f in files:
            text = paths.load_prompt(f.stem)
            self.assertTrue(text.strip(), f"prompts/{f.name} is empty")

        # The two genre meta-prompts are positionally formatted with a fixed
        # placeholder set; a typo in extraction would raise KeyError here.
        pass1 = paths.load_prompt("genre_framework_pass1")
        pass1.format(
            genre_description="g", chapter_count=24, estimated_words=78000,
            words_per_chapter=3200, user_directives_block="",
        )
        pass2 = paths.load_prompt("genre_framework_pass2")
        pass2.format(
            genre_config="{}", genre_description="g", chapter_count=24,
            estimated_words=78000, words_per_chapter=3200,
            user_directives_block="", beats_per_chapter=5, words_per_beat=640,
        )

    # -- 10. new LLM-output schemas ----------------------------------------

    def test_llm_output_schemas(self):
        self._real_root()
        from core.validation import (
            AdversarialCuts, OutputValidationError, ReaderPanelAnswers,
            SanitizedTitles, SlopRepairPatch, TitleJudgePanel, TitleScoreMap,
            parse_validated,
        )

        judges = [{"key": f"j{i}", "name": f"J{i}", "persona": "p"}
                  for i in range(4)]
        panel = parse_validated(TitleJudgePanel, json.dumps(judges), context="t")
        self.assertEqual(len(panel.root), 4)
        with self.assertRaises(OutputValidationError):
            parse_validated(TitleJudgePanel, json.dumps(judges[:3]), context="t")

        scores = parse_validated(TitleScoreMap, '{"A": "85", "B": 92}', context="t")
        self.assertEqual(scores.root, {"A": 85, "B": 92})

        titles = parse_validated(SanitizedTitles, '{"1": "One", "2": " Two "}',
                                 context="t")
        self.assertEqual(titles.root, {1: "One", 2: "Two"})

        patch = parse_validated(SlopRepairPatch, '{"p1": "new text"}', context="t")
        self.assertEqual(patch.root, {"p1": "new text"})
        with self.assertRaises(OutputValidationError):
            parse_validated(SlopRepairPatch, '{"p1": "   "}', context="t")

        cuts = parse_validated(AdversarialCuts, '{"cuts": [{"quote": "x"}]}',
                               context="t")
        self.assertEqual(cuts.cuts[0].quote, "x")
        self.assertEqual(cuts.overall_fat_percentage, 0.0)

        answers = parse_validated(
            ReaderPanelAnswers,
            '{"momentum_loss": "Ch 3", "worst_scene": null}', context="t")
        self.assertEqual(answers.momentum_loss, "Ch 3")
        self.assertEqual(answers.worst_scene, "")


if __name__ == "__main__":
    unittest.main()
