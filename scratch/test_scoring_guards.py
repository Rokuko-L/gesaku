#!/usr/bin/env python3
"""Regression tests for the foundation plateau detector.

Bug being guarded: the keep/discard check used score >= best_score, so a TIE
reset foundation_stall_count. A judge returning identical 6.0 every iteration
(the exact motivating case for plateau detection) looped forever. The decision
now lives in pipeline_infra.foundation_plateau with strict improvement, and
these tests simulate full iteration sequences through it.

Run: uv run python -m unittest scratch.test_scoring_guards
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import validation
from pipeline.pipeline_infra import (
    FOUNDATION_PLATEAU_ITERS,
    foundation_plateau,
)


def run_foundation_loop(scores, start_best=0.0, threshold=7.5):
    """Simulate the run_foundation keep/discard/exit logic offline."""
    best, stall = start_best, 0
    for i, score in enumerate(scores, 1):
        keep, best, stall = foundation_plateau(score, best, stall)
        if best >= threshold:
            return f"passed@{i}"
        if stall >= FOUNDATION_PLATEAU_ITERS:
            return f"plateau@{i}"
    return "exhausted"


class PlateauDetectorTest(unittest.TestCase):
    def test_tie_is_a_stall(self):
        keep, best, stall = foundation_plateau(6.0, 6.0, 0)
        self.assertFalse(keep)
        self.assertEqual(best, 6.0)
        self.assertEqual(stall, 1)

    def test_strict_improvement_resets_stall(self):
        keep, best, stall = foundation_plateau(6.5, 6.0, 2)
        self.assertTrue(keep)
        self.assertEqual(best, 6.5)
        self.assertEqual(stall, 0)

    def test_decrease_discards_and_stalls(self):
        keep, best, stall = foundation_plateau(5.5, 6.0, 1)
        self.assertFalse(keep)
        self.assertEqual(best, 6.0)
        self.assertEqual(stall, 2)

    def test_flat_scores_exit_early(self):
        """The motivating bug: identical 6.0 across 5 iterations must hit the
        plateau exit instead of burning all iterations."""
        self.assertEqual(run_foundation_loop([6.0] * 5), "plateau@4")

    def test_flat_scores_from_resume_state(self):
        """Same, starting from a prior best (resume mid-run)."""
        self.assertEqual(run_foundation_loop([6.0] * 5, start_best=6.0),
                         "plateau@3")

    def test_decrease_then_ties_accumulate(self):
        """A discard followed by ties accumulates stalls into an exit."""
        self.assertEqual(run_foundation_loop([6.5, 6.0, 6.0, 6.0]), "plateau@4")

    def test_real_progress_passes_threshold(self):
        self.assertEqual(
            run_foundation_loop([6.0, 6.8, 7.0, 7.6]), "passed@4")

    def test_improving_scores_never_plateau(self):
        self.assertEqual(run_foundation_loop([5.0 + 0.1 * i for i in range(10)]),
                         "exhausted")


class ScoreCoercionTest(unittest.TestCase):
    """Table tests for ScoreOutput._coerce_score / NovelScoreOutput._coerce_score.

    Bug being guarded: the coercer used rstrip("/10"), which strips a
    CHARACTER SET, not a suffix — "6.1" parsed as 6.0, "7.11" as 7.0, and a
    perfect "10" crashed on float(""). Both models share the same logic, so
    every case runs against both.
    """

    VALID = [
        ("8.2", 8.2),
        ("8.2/10", 8.2),      # suffix form still works
        ("6.1", 6.1),         # trailing digit is NOT part of a "/10" set
        ("7.11", 7.11),
        ("10", 10.0),         # a perfect score must not crash
        ("9/10", 9.0),
        (" 9.5 /10 ", 9.5),   # surrounding whitespace tolerated
        (7, 7.0),             # numeric passthrough
        (6.5, 6.5),
    ]

    def test_both_models_parse_scores_exactly(self):
        for model in (validation.ScoreOutput, validation.NovelScoreOutput):
            field = "overall_score" if model is validation.ScoreOutput \
                else "novel_score"
            for raw, expected in self.VALID:
                with self.subTest(model=model.__name__, input=raw):
                    parsed = model.model_validate({field: raw})
                    self.assertAlmostEqual(getattr(parsed, field), expected)

    def test_empty_after_suffix_still_rejected(self):
        """'/10' alone has no score left — must fail validation, not crash."""
        with self.assertRaises(Exception) as ctx:
            validation.ScoreOutput.model_validate({"overall_score": "/10"})
        self.assertNotIsInstance(ctx.exception, TypeError)

    def test_out_of_range_still_rejected(self):
        with self.assertRaises(Exception):
            validation.ScoreOutput.model_validate({"overall_score": 11})
        with self.assertRaises(Exception):
            validation.NovelScoreOutput.model_validate({"novel_score": -1})


class RunFoundationLoopTest(unittest.TestCase):
    """Drives the REAL run_foundation loop offline (reviewer caveat).

    The simulation tests above guard foundation_plateau() itself; this one
    guards the CALL SITE: heavy workers are stubbed, but run_foundation's
    actual keep/discard/exit logic executes. If someone later removes the
    foundation_plateau call (or reinlines a >= comparison), flat scores stop
    exiting early and THESE tests fail instead of staying green.
    """

    def _run_loop(self, scores):
        """Run run_foundation with stubbed workers feeding canned eval scores.

        Returns (state, calls) where calls records git plumbing.
        """
        import tempfile
        from types import SimpleNamespace
        import run_pipeline as rp
        from pipeline.phases import foundation as foundation_phase
        from core import paths as paths_mod

        tmp = Path(tempfile.mkdtemp(prefix="gesaku_loop_"))
        (tmp / "projects" / "looptest").mkdir(parents=True)
        orig_root = paths_mod._root_dir
        paths_mod._root_dir = tmp
        paths_mod.set_project_name("looptest")
        paths_mod.get_outline_path().write_text("outline body", encoding="utf-8")

        eval_count = {"n": 0}
        calls = {"commits": [], "resets": [], "saves": []}

        def fake_uv_run(script, timeout=None):
            if "evaluate.py" in script:
                eval_count["n"] += 1
                score = scores[min(eval_count["n"], len(scores)) - 1]
                return SimpleNamespace(
                    stdout=f"overall_score: {score}\nlore_score: 5.0\n",
                    returncode=0)
            return SimpleNamespace(stdout="", returncode=0)

        patches = {
            "uv_run": fake_uv_run,
            "banner": lambda *a, **k: None,
            "step": lambda *a, **k: None,
            "load_state": lambda: {"title": "T"},
            "load_genre": lambda: {"framework": {"premise_arc_beats": []}},
            "validate_premise_beats": lambda beats, text: (True, ""),
            "git_add_commit": lambda msg: calls["commits"].append(msg),
            "git_reset_hard": lambda ref="HEAD": calls["resets"].append(ref),
            "save_state": lambda s: calls["saves"].append(dict(s)),
        }
        saved = {name: getattr(foundation_phase, name) for name in patches}
        try:
            for name, fn in patches.items():
                setattr(foundation_phase, name, fn)
            state = rp.run_foundation({"iteration": 0})
        finally:
            for name, fn in saved.items():
                setattr(foundation_phase, name, fn)
            paths_mod._root_dir = orig_root
        return state, calls

    def test_flat_scores_exit_via_real_loop(self):
        """The motivating case through the real loop: identical 6.0 must hit
        the plateau exit at iteration 4 (keep@1, tie-stalls @2..4), not burn
        MAX_FOUNDATION_ITERS."""
        state, calls = self._run_loop([6.0] * 10)
        self.assertEqual(state["iteration"], 4)
        self.assertEqual(state["foundation_stall_count"], 3)
        self.assertEqual(state["phase"], "drafting")
        self.assertEqual(len(calls["resets"]), 3)   # ties were discarded
        self.assertEqual(calls["commits"][0],       # initial setup commit
                         "initial project setup (seed, config)")

    def test_threshold_pass_exits_via_real_loop(self):
        state, calls = self._run_loop([7.6])
        self.assertEqual(state["iteration"], 1)
        self.assertGreaterEqual(state["foundation_score"], 7.5)
        self.assertEqual(calls["resets"], [])


if __name__ == "__main__":
    unittest.main()
