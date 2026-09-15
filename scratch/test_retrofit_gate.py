"""Offline tests for post-reveal retrofit blocking and continuity report.

Run: uv run python -m unittest scratch.test_retrofit_gate
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core import paths
from core import canon as canon_mod
from core import plant_hygiene
from pipeline import retrofit_reveal


CANON = """## Foundation

- visible_from=1: The capital sits on a salted plain.
- visible_from=14: B is the architect of the succession plot; A is the public face.
"""

OUTLINE_STARVED = """# T

## DETAILED CHAPTER OUTLINES

### Chapter 1: A
1. POV: A
2. Characters: A
"""

OUTLINE_OK = """# T

## DETAILED CHAPTER OUTLINES

### Chapter 1: A
1. POV: A
2. Characters: A, B
5. Scene Beats:
   1. A waits. B watches from the archway.

### Chapter 2: B
1. POV: A
2. Characters: A, B
5. Scene Beats:
   1. B enters, takes the letter, leaves.
"""

CHARACTERS = "# Characters\n\n## A\n\n## B\n"


class ContinuityReportTest(unittest.TestCase):
    def test_no_sealed_is_quiet(self):
        parsed = canon_mod.parse_canon("## Foundation\n\n- visible_from=1: public\n")
        report = retrofit_reveal.continuity_report(parsed, OUTLINE_OK, {}, 14)
        self.assertEqual(report["mask_breaks"], [])
        self.assertEqual(report["plant_gaps"], [])

    def test_detects_meaning_frame_in_pre_reveal_prose(self):
        parsed = canon_mod.parse_canon(CANON)
        chapters = {1: "A walked in. B secretly controlled the succession."}
        report = retrofit_reveal.continuity_report(parsed, OUTLINE_OK, chapters, 14)
        self.assertTrue(report["mask_breaks"])
        # Informational only — flag must not claim it gates keeps
        self.assertIn("never gates", report["note"])


class CoverageBlockTest(unittest.TestCase):
    def test_starved_outline_fails_coverage(self):
        parsed = canon_mod.parse_canon(CANON)
        names = canon_mod.action_plant_characters(parsed, CHARACTERS)
        self.assertIn("B", names)
        cov = plant_hygiene.action_plant_coverage(OUTLINE_STARVED, names, 14)
        self.assertFalse(cov["pass"])

    def test_ok_outline_passes_coverage(self):
        parsed = canon_mod.parse_canon(CANON)
        names = canon_mod.action_plant_characters(parsed, CHARACTERS)
        cov = plant_hygiene.action_plant_coverage(OUTLINE_OK, names, 14)
        self.assertTrue(cov["pass"], cov["problems"])


if __name__ == "__main__":
    unittest.main()
