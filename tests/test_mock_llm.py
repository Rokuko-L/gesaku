#!/usr/bin/env python3
"""Offline tests for the mock-LLM harness + validation retry integration.

Run: uv run python -m unittest tests.test_mock_llm -v
No network, no API key required.
"""

from core import llm
from core import paths
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import validation
from core.mock_llm import MockLLM


class MockLLMTest(unittest.TestCase):
    def test_in_order_consumption(self):
        mock = MockLLM()
        mock.add("first").add("second")
        with mock.install():
            self.assertEqual(llm.call_llm("p1"), "first")
            self.assertEqual(llm.call_llm("p2"), "second")
        # restored after context exit
        self.assertIsNot(llm.call_llm, mock)

    def test_match_substring(self):
        mock = MockLLM()
        mock.add("compare-response", match="Compare these")
        mock.add("fallback")
        with mock.install():
            self.assertEqual(llm.call_llm("random prompt"), "fallback")
            self.assertEqual(llm.call_llm("Compare these two chapters"), "compare-response")

    def test_unexpected_call_raises(self):
        mock = MockLLM()
        with mock.install():
            with self.assertRaises(AssertionError):
                llm.call_llm("not planned")

    def test_rebinds_import_time_references(self):
        """Modules that did 'from llm import call_llm' get the mock too."""
        from core import llm
        import pipeline.evaluate as evaluate  # binds call_llm into its own namespace at import
        mock = MockLLM()
        mock.add_json({"overall_score": 7.0})
        with mock.install():
            self.assertIs(evaluate.call_llm, mock)
            self.assertIs(llm.call_llm, mock)
        self.assertIs(evaluate.call_llm, llm.call_llm)


class ValidationRetryIntegrationTest(unittest.TestCase):
    """call_judge_json must feed schema failures back into the retry loop."""

    def setUp(self):
        import unittest.mock
        stub = {
            "identity": {"evaluator_system": "You are a literary critic."},
            "perspective": "",
        }
        self._cm = unittest.mock.patch("pipeline.evaluate.load_genre", return_value=stub)
        self._cm.start()
        self.addCleanup(self._cm.stop)

    def test_invalid_schema_triggers_self_correction(self):
        import pipeline.evaluate as evaluate
        mock = MockLLM()
        # attempt 1: valid JSON, wrong shape (missing overall_score)
        mock.add('{"foo": "bar"}')
        # attempt 2: correct shape, score as string (coerced by validator)
        mock.add('{"overall_score": "8.2", "prose_quality": {"score": 9}}')
        with mock.install():
            result = evaluate.call_judge_json("judge prompt", model=validation.ScoreOutput)
        self.assertEqual(result["overall_score"], 8.2)
        self.assertEqual(len(mock.calls), 2)
        # second call is a fix-prompt quoting the validation feedback
        second = mock.last_prompt()
        self.assertIn("overall_score", second)
        self.assertIn("Judge response", second)

    def test_syntax_error_then_success(self):
        import pipeline.evaluate as evaluate
        mock = MockLLM()
        mock.add("this is not json at all")
        mock.add('{"overall_score": 6.5}')
        with mock.install():
            result = evaluate.call_judge_json("prompt", model=validation.ScoreOutput)
        self.assertEqual(result["overall_score"], 6.5)

    def test_exhausted_retries_raise(self):
        import pipeline.evaluate as evaluate
        mock = MockLLM()
        for _ in range(3):
            mock.add('{"nope": 1}')
        with mock.install():
            with self.assertRaises(validation.OutputValidationError):
                evaluate.call_judge_json("prompt", retries=3, model=validation.ScoreOutput)
        self.assertEqual(len(mock.calls), 3)

    def test_compare_chapters_end_to_end(self):
        """Full compare() path: real project files, mocked judge."""
        import pipeline.compare_chapters as compare_chapters
        project = "mocktest_compare"
        orig_name = paths._project_name
        try:
            paths.set_project_name(project)
            ch_dir = paths.get_chapters_dir()
            (ch_dir / "ch_01.md").write_text("# Chapter 1: Alpha\n\nFirst chapter text.", encoding="utf-8")
            (ch_dir / "ch_02.md").write_text("# Chapter 2: Beta\n\nSecond chapter text.", encoding="utf-8")

            mock = MockLLM()
            mock.add(json.dumps({
                "winner": "A", "winner_chapter": 1, "margin": "clear",
                "decisive_moment": "the whole thing", "winner_strength": "s",
                "loser_weakness": "w", "best_sentence_a": "a", "best_sentence_b": "b",
            }))
            with mock.install():
                result = compare_chapters.compare(1, 2)
            self.assertEqual(result["winner"], "A")
            self.assertEqual(result["winner_chapter"], 1)
            self.assertEqual(result["ch_a"], 1)
            self.assertIn("CHAPTER A", mock.last_prompt())
        finally:
            paths._project_name = orig_name
            os.environ.pop("GESAKU_PROJECT", None)
            shutil.rmtree(paths.get_project_dir(), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
