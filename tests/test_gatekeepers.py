#!/usr/bin/env python3
"""Behavioral tests for outline gatekeepers.

Regression guard for the silent-gatekeeper bug: verify_tonal_drift's lazy
import broke during the core/ package migration, and its broad except
converted the crash into a silent "no drift" verdict — every test stayed
green while a validation gate was disabled. These tests exercise the gate
end-to-end with MockLLM so a dead gate FAILS instead of passing vacuously.

Run: uv run python -m unittest tests.test_gatekeepers
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import validation
from core.mock_llm import MockLLM
from foundation.gen_outline import _act_ranges, verify_tonal_drift


class TonalDriftGateTest(unittest.TestCase):
    ROADMAP = ("## HIGH-LEVEL ROADMAP\n\n### Chapter 1: It begins\n\n"
               "## GLOBAL PLOT THREADS LEDGER\n\n- thread_one: planted ch1")

    def test_no_drift_verdict_passes_gate(self):
        mock = MockLLM()
        mock.add_json({"has_drift": False, "analysis": "consistent", "violations": []})
        with mock.install():
            has_drift, feedback = verify_tonal_drift(
                self.ROADMAP, "seed", "genre", total_chapters=24)
        self.assertFalse(has_drift)
        self.assertEqual(feedback, "")
        self.assertEqual(len(mock.calls), 1)  # the judge was actually consulted

    def test_drift_verdict_blocks_gate(self):
        """If the lazy import inside verify_tonal_drift breaks again, the
        broad except returns (False, '') and THIS assertion fails."""
        mock = MockLLM()
        mock.add_json({"has_drift": True, "analysis": "...",
                       "violations": ["magic rules violated in act 3"]})
        with mock.install():
            has_drift, feedback = verify_tonal_drift(
                self.ROADMAP, "seed", "genre", total_chapters=24)
        self.assertTrue(has_drift)
        self.assertIn("magic rules violated", feedback)

    def test_short_books_skip_without_llm_call(self):
        mock = MockLLM()
        with mock.install():
            has_drift, feedback = verify_tonal_drift(
                self.ROADMAP, "seed", "genre", total_chapters=2)
        self.assertFalse(has_drift)
        self.assertEqual(len(mock.calls), 0)

    def test_act_ranges_boundaries(self):
        self.assertEqual(_act_ranges(4), ((1, 1), (2, 3), (4, 4)))
        self.assertEqual(_act_ranges(24), ((1, 6), (7, 18), (19, 24)))
        self.assertIsNone(_act_ranges(2))


if __name__ == "__main__":
    unittest.main()
