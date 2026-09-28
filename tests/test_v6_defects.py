"""Regression tests for the defects a 30-chapter first-person run exposed.

Every test here corresponds to something that actually went wrong in
`sir the confortable v6 fp`, where the run finished at novel_score 7.67 with
its narrator lock off for all 30 chapters.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foundation.gen_outline_part2 import missing_field_labels

# The shape gen_outline actually emits.
GOOD_CHAPTER = """### Chapter {n}: A Good Title
1. Focus: Lily Bakersville
2. MC presence: on-page
3. Scene type: confrontation
4. Characters: Lily, Kael, Mira
5. Emotional Arc: Confidence -> Doubt
6. Summary: Something happens that matters.
7. Orientation Facts:
  - The treaty was signed in the north tower.
8. Scene Stakes: The alliance holds or does not.
9. Scene Beats:
1. Lily tests the boundary and it holds.
2. Kael arrives and the terms change.
10. Plants & Harvests: [Plant: treaty_clause - "The clause is quoted later."]
"""

# What the refinement pass actually produced: every label dissolved into prose.
# Textually complete, functionally broken.
DISSOLVED_CHAPTER = """### Chapter {n}: A Good Title
Lily Bakersville is the focus of this chapter, and she is on-page throughout.
Kael and Mira also appear. The scene type is a confrontation.
Her arc runs from confidence to doubt. Something happens that matters, and
the treaty signed in the north tower is established as prior knowledge.
The stakes are the alliance. Kael arrives and the terms change.
"""


def block(chapters=(1, 2, 3), template=GOOD_CHAPTER):
    return "\n\n---\n\n".join(template.format(n=n) for n in chapters)


class FieldLabelPreservationTest(unittest.TestCase):
    """The refinement pass must not dissolve the field labels.

    This is the root cause of the dead narrator lock: `Focus:` and
    `Characters:` are how the MC is identified, and `Scene type:` is how the
    climax ceiling is applied. A run whose refined outline had zero labels
    produced 30 chapters with no narrator checking at all, and reported a
    healthy score.
    """

    def test_a_well_formed_block_reports_nothing_missing(self):
        self.assertEqual(missing_field_labels(block(), 1, 3), [])

    def test_a_dissolved_block_is_caught(self):
        lost = missing_field_labels(block(template=DISSOLVED_CHAPTER), 1, 3)
        self.assertTrue(lost, "a fully dissolved block must be rejected")
        self.assertIn("Chapter 1", lost[0])

    def test_it_names_the_labels_that_vanished(self):
        lost = " ".join(missing_field_labels(block(template=DISSOLVED_CHAPTER), 1, 1))
        self.assertIn("Focus", lost)
        self.assertIn("Scene type", lost)
        self.assertIn("Characters", lost)

    def test_a_single_missing_label_is_still_a_failure(self):
        text = block().replace("3. Scene type: confrontation", "")
        lost = missing_field_labels(text, 1, 3)
        self.assertEqual(len(lost), 3)
        self.assertIn("Scene type", lost[0])

    def test_labels_may_keep_their_list_numbering(self):
        # gen_outline emits "1. Focus:", the drafted form has "**Focus**:".
        # Both must parse, or a legal reformat reads as data loss.
        for form in ("1. Focus: Lily Bakersville",
                     "- **Focus**: Lily Bakersville",
                     "**Focus**: Lily Bakersville",
                     "Focus: Lily Bakersville"):
            text = block().replace("1. Focus: Lily Bakersville", form)
            self.assertEqual(missing_field_labels(text, 1, 1), [], form)

    def test_chapters_outside_the_range_are_not_inspected(self):
        text = block(chapters=(1, 2, 3, 4, 5))
        self.assertEqual(missing_field_labels(text, 1, 2), [])


class RefinedOutlineIsUsableTest(unittest.TestCase):
    """A refined outline must still resolve a protagonist.

    `mc_aliases_for_project` is the sole input to the narrator lock. An empty
    result is not "clean" — it means the guard silently switched itself off.
    """

    OUTLINE_WITH_LABELS = (
        "# TITLE\n\n## DETAILED CHAPTER OUTLINES\n\n" + block(chapters=(1, 2))
    )
    OUTLINE_WITHOUT = (
        "# TITLE\n\n## DETAILED CHAPTER OUTLINES\n\n"
        + block(chapters=(1, 2), template=DISSOLVED_CHAPTER)
    )

    def test_labelled_outline_resolves_the_mc(self):
        from core.outline import mc_aliases_for_project
        aliases = mc_aliases_for_project(self.OUTLINE_WITH_LABELS, "")
        self.assertTrue(aliases, "a labelled outline must yield aliases")
        self.assertIn("lily", aliases)

    def test_dissolved_outline_yields_nothing_and_says_so(self):
        from core.outline import mc_aliases_for_project
        self.assertEqual(mc_aliases_for_project(self.OUTLINE_WITHOUT, ""), set())


class AdversarialCutBudgetTest(unittest.TestCase):
    """`adversarial_edit` had a flat 8k cap against a 12.7k natural length.

    26 of 90 calls truncated, which was 23.6% of the revision phase's model
    time spent producing output that was thrown away and regenerated.
    """

    def test_the_budget_exceeds_the_observed_mean_output(self):
        from pipeline.adversarial_edit import CUT_BASE_TOKENS, CUT_MAX_TOKENS
        self.assertGreater(CUT_BASE_TOKENS, 12723)
        self.assertGreater(CUT_MAX_TOKENS, CUT_BASE_TOKENS)

    def test_truncation_escalates_rather_than_repeating(self):
        # Retrying the same budget is guaranteed to truncate again; the
        # evaluate.py precedent is to grow the budget and re-ask.
        import inspect
        from pipeline.adversarial_edit import call_judge
        src = inspect.getsource(call_judge)
        self.assertIn("TruncationError", src)
        self.assertIn("1.5", src)

    def test_the_old_flat_cap_is_gone(self):
        src = Path("pipeline/adversarial_edit.py").read_text(encoding="utf-8")
        self.assertNotIn("max_tokens=8000", src)


class ArcSummaryCacheTest(unittest.TestCase):
    """`build_arc_summary` re-summarized all 30 chapters on every invocation.

    120 calls over 46 distinct chapter texts — 74 calls on chapters that had
    not changed since the previous cycle.
    """

    def test_a_summary_is_reused_when_the_chapter_is_unchanged(self):
        import inspect
        import pipeline.build_arc_summary as m
        src = inspect.getsource(m)
        self.assertIn("sha256", src)

    def test_the_cache_key_is_content_not_mtime(self):
        # git_reset_hard reverts file mtimes constantly in this pipeline, so an
        # mtime key would miss every re-run after a revert.
        import inspect
        import pipeline.build_arc_summary as m
        src = inspect.getsource(m)
        self.assertNotIn("st_mtime", src)


if __name__ == "__main__":
    unittest.main()
