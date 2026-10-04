"""Regression tests for the defects a 30-chapter first-person run exposed.

Every test here corresponds to something that actually went wrong in
`sir the confortable v6 fp`, where the run finished at novel_score 7.67 with
its narrator lock off for all 30 chapters.

These are behavioural: they call the real functions and assert on real
output. An earlier draft asserted on `inspect.getsource(...)`, which passes
for any implementation that merely mentions a word.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foundation.gen_outline_part2 import (
    _FIELD_LABELS, missing_field_labels, validate_block_output,
)

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

# What the refinement pass actually produced: every label dissolved into
# prose. Textually complete, functionally broken.
DISSOLVED_CHAPTER = """### Chapter {n}: A Good Title
Lily Bakersville is the focus of this chapter, and she is on-page throughout.
Kael and Mira also appear. The scene type is a confrontation.
Her arc runs from confidence to doubt. Something happens that matters, and
the treaty signed in the north tower is established as prior knowledge.
The stakes are the alliance. Kael arrives and the terms change.
"""


def block(chapters=(1, 2, 3), template=GOOD_CHAPTER, marker=True):
    body = "\n\n---\n\n".join(template.format(n=n) for n in chapters)
    head = "## DETAILED CHAPTER OUTLINES\n\n" if marker else ""
    return head + body


class FieldLabelPreservationTest(unittest.TestCase):
    """The refinement pass must not dissolve the field labels.

    Root cause of the dead narrator lock: `Focus:` and `Characters:` are how
    the MC is identified, and `Scene type:` is how the climax ceiling is
    applied. A run whose refined outline had zero labels produced 30 chapters
    with no narrator checking at all, and reported a healthy score.
    """

    def test_a_well_formed_block_reports_nothing_missing(self):
        self.assertEqual(missing_field_labels(block(), 1, 3), [])

    def test_a_dissolved_block_is_caught(self):
        lost = missing_field_labels(block(template=DISSOLVED_CHAPTER), 1, 3)
        self.assertTrue(lost, "a fully dissolved block must be rejected")
        self.assertIn("Chapter 1", lost[0])

    def test_it_names_the_labels_that_vanished(self):
        lost = " ".join(
            missing_field_labels(block(template=DISSOLVED_CHAPTER), 1, 1))
        self.assertIn("Focus", lost)
        self.assertIn("Scene type", lost)
        self.assertIn("Characters", lost)

    def test_a_single_missing_label_is_still_a_failure(self):
        text = block().replace("3. Scene type: confrontation", "")
        lost = missing_field_labels(text, 1, 3)
        self.assertEqual(len(lost), 3)
        self.assertIn("Scene type", lost[0])

    def test_pov_is_accepted_as_a_focus_alias(self):
        # core/outline.py has always accepted `POV:` for `Focus:` and documents
        # it. Gating on the strict spelling fails a book over a rename the
        # narrator lock itself treats as equivalent.
        for form in ("POV: Lily Bakersville", "1. POV: Lily Bakersville",
                     "- **POV:** Lily Bakersville"):
            text = block().replace("1. Focus: Lily Bakersville", form)
            self.assertEqual(missing_field_labels(text, 1, 1), [], form)

    def test_labels_may_keep_their_numbering_and_emphasis(self):
        for form in ("1. Focus: Lily Bakersville",
                     "- **Focus**: Lily Bakersville",
                     "**Focus**: Lily Bakersville",
                     "Focus: Lily Bakersville"):
            text = block().replace("1. Focus: Lily Bakersville", form)
            self.assertEqual(missing_field_labels(text, 1, 1), [], form)

    def test_an_empty_label_value_is_a_failure(self):
        # A label with nothing after the colon tells the tooling nothing, and
        # reading it as present is the "reports clean while functionally empty"
        # failure this file exists to prevent.
        text = block().replace("1. Focus: Lily Bakersville", "1. Focus:")
        self.assertIn("Focus", " ".join(missing_field_labels(text, 1, 1)))

    def test_a_trailing_ledger_cannot_lend_labels_to_the_last_chapter(self):
        ledger = ("\n## FORESHADOWING LEDGER\n\n"
                  + "\n".join(f"- {lab}: something" for lab in _FIELD_LABELS))
        text = block(chapters=(1, 2), template=DISSOLVED_CHAPTER) + ledger
        self.assertEqual(len(missing_field_labels(text, 1, 2)), 2)

    def test_chapters_outside_the_range_are_not_inspected(self):
        self.assertEqual(
            missing_field_labels(block(chapters=(1, 2, 3, 4, 5)), 1, 2), [])

    def test_the_roadmap_section_is_not_mistaken_for_detailed_entries(self):
        # The HIGH-LEVEL ROADMAP carries a `### Chapter N: <slug>` per chapter
        # with no labels. Without the scope guard every chapter reads broken.
        roadmap = ("## HIGH-LEVEL ROADMAP\n\n"
                   + "\n".join(f"### Chapter {n}: slug_{n}"
                               for n in range(1, 31)))
        self.assertEqual(
            missing_field_labels(roadmap + "\n\n" + block(chapters=(1, 2, 3)),
                                1, 3), [])

    def test_only_labels_with_a_consumer_are_required(self):
        # `Emotional Arc`, `Summary`, `Scene Stakes` and `MC presence` have no
        # consumer; gating on them fails books over labels nothing reads.
        for unconsumed in ("Emotional Arc", "Summary", "Scene Stakes",
                           "MC presence"):
            self.assertNotIn(unconsumed, _FIELD_LABELS)
            text = block().replace(f"5. {unconsumed}:", "5. (removed):")
            self.assertEqual(missing_field_labels(text, 1, 1), [], unconsumed)

    def test_validate_rejects_a_dissolved_block_with_actionable_text(self):
        ok, err = validate_block_output(block(template=DISSOLVED_CHAPTER), 1, 3)
        self.assertFalse(ok)
        self.assertIn("Focus", err)
        self.assertIn("Re-emit", err)

    def test_validate_accepts_the_good_block(self):
        ok, err = validate_block_output(block(), 1, 3)
        self.assertTrue(ok, err)


class RefinedOutlineIsUsableTest(unittest.TestCase):
    """A refined outline must still resolve a protagonist.

    `mc_aliases_for_project` is the sole input to the narrator lock. An empty
    result is not "clean" — it means the guard silently switched itself off.
    """

    def test_labelled_outline_resolves_the_mc(self):
        from core.outline import mc_aliases_for_project
        aliases = mc_aliases_for_project(block(chapters=(1, 2)), "")
        self.assertTrue(aliases, "a labelled outline must yield aliases")
        self.assertIn("lily", aliases)

    def test_dissolved_outline_yields_nothing(self):
        from core.outline import mc_aliases_for_project
        self.assertEqual(
            mc_aliases_for_project(
                block(chapters=(1, 2), template=DISSOLVED_CHAPTER), ""),
            set())


class ArcSummaryCacheTest(unittest.TestCase):
    """`build_arc_summary` re-summarized all 30 chapters on every invocation.

    120 calls over 46 distinct chapter texts — 74 calls on chapters that had
    not changed since the previous cycle.
    """

    def test_an_unchanged_chapter_is_not_resummarized(self):
        import pipeline.build_arc_summary as m
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ch_01.md"
            p.write_text("Chapter one prose. " * 40, encoding="utf-8")
            calls = []
            orig = m.call_writer
            m.call_writer = lambda prompt, max_tokens=4000: (
                calls.append(prompt) or "A summary.")
            try:
                cache = {}
                first = m.process_chapter_arc_summary(p, 1, cache)
                second = m.process_chapter_arc_summary(p, 1, cache)
            finally:
                m.call_writer = orig
            self.assertEqual(len(calls), 1,
                             "an unchanged chapter was re-summarized")
            self.assertEqual(first[1], second[1])

    def test_two_chapters_with_identical_text_keep_their_own_numbers(self):
        # The rendered entry embeds `### Chapter {n}`, so a cache keyed on text
        # alone files chapter 8's summary under chapter 7's heading — and the
        # reader panel targets rewrites by chapter number.
        import pipeline.build_arc_summary as m
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for n in (7, 8):
                (d / f"ch_{n:02d}.md").write_text("Identical prose. " * 40,
                                                   encoding="utf-8")
            cache = {}
            orig = m.call_writer
            m.call_writer = lambda prompt, max_tokens=4000: "A summary."
            try:
                _, e7 = m.process_chapter_arc_summary(d / "ch_07.md", 7, cache)
                _, e8 = m.process_chapter_arc_summary(d / "ch_08.md", 8, cache)
            finally:
                m.call_writer = orig
            self.assertIn("### Chapter 7", e7.splitlines()[0])
            self.assertIn("### Chapter 8", e8.splitlines()[0])

    def test_a_changed_chapter_is_resummarized(self):
        import pipeline.build_arc_summary as m
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ch_01.md"
            cache = {}
            calls = []
            orig = m.call_writer
            m.call_writer = lambda prompt, max_tokens=4000: (
                calls.append(prompt) or "A summary.")
            try:
                p.write_text("First version. " * 30, encoding="utf-8")
                m.process_chapter_arc_summary(p, 1, cache)
                p.write_text("Second version, different. " * 30,
                             encoding="utf-8")
                m.process_chapter_arc_summary(p, 1, cache)
            finally:
                m.call_writer = orig
            self.assertEqual(len(calls), 2)

    def test_the_cache_is_bounded(self):
        import pipeline.build_arc_summary as m
        cache = {str(i): f"entry {i}" for i in range(5000)}
        m._prune_cache(cache, keep=30)
        self.assertLessEqual(
            len(cache),
            max(m.CACHE_MIN_ENTRIES, 30 * m.CACHE_MAX_ENTRIES_MULTIPLE))

    def test_pruning_never_evicts_what_the_current_run_needs(self):
        import pipeline.build_arc_summary as m
        cache = {str(i): f"entry {i}" for i in range(100)}
        m._prune_cache(cache, keep=100)
        self.assertEqual(len(cache), 100)

    def test_the_cache_key_is_content_not_mtime(self):
        # git_reset_hard reverts mtimes constantly, so an mtime key would miss
        # every re-run after a revert.
        src = Path("pipeline/build_arc_summary.py").read_text(encoding="utf-8")
        self.assertIn("sha256", src)
        self.assertNotIn("st_mtime", src)

    def test_the_cache_is_not_tracked_by_git(self):
        # `git add -A` runs on every chapter commit, so tracking this would
        # write a copy on ~90 commits per run and let `git reset --hard`
        # resurrect a stale cache over fresh chapters.
        src = Path("pipeline/pipeline_infra.py").read_text(encoding="utf-8")
        self.assertIn("arc_summary_cache.json", src)


class AdversarialCutBudgetTest(unittest.TestCase):
    """`adversarial_edit` had a flat 8k cap against a 12.7k natural length.

    26 of 90 calls truncated, a large share of the revision phase's model time
    spent producing output that was thrown away and regenerated.
    """

    def test_the_budget_exceeds_the_observed_maximum(self):
        from pipeline.adversarial_edit import CUT_BASE_TOKENS
        # Observed max for this call site across 90 calls was 17,939.
        self.assertGreater(CUT_BASE_TOKENS, 17939)

    def test_retrying_fits_the_subprocess_envelope(self):
        # revision.py runs this script under ONE wall-clock cap. With the LLM
        # and subprocess budgets set equal, retries multiply the worst case, so
        # a high retry count turns every truncation into a guaranteed timeout
        # — and the cut file is never written, so apply_cuts skips the chapter.
        from pipeline.adversarial_edit import CUT_RETRIES
        self.assertLessEqual(CUT_RETRIES, 2)

    def test_truncation_escalates_and_then_reraises(self):
        import pipeline.adversarial_edit as m
        seen = []

        def fake(**kw):
            seen.append(kw["max_tokens"])
            if len(seen) < 2:
                raise m.TruncationError("cut off")
            return "OK"

        orig = m.call_llm
        try:
            m.call_llm = fake
            self.assertEqual(m.call_judge("p"), "OK")
            self.assertEqual(
                seen, [m.CUT_BASE_TOKENS, int(m.CUT_BASE_TOKENS * 1.5)])
            m.call_llm = lambda **kw: (_ for _ in ()).throw(
                m.TruncationError("x"))
            with self.assertRaises(m.TruncationError):
                m.call_judge("p")
        finally:
            m.call_llm = orig

    def test_the_escalation_message_reports_the_current_budget(self):
        import inspect
        from pipeline.adversarial_edit import call_judge
        # It used to print the original budget twice, so attempt 2 claimed to
        # jump 20000 -> 40000 after already having spent 30000.
        self.assertIn("({budget} -> {new_budget})",
                      inspect.getsource(call_judge))

    def test_the_old_flat_cap_is_gone(self):
        src = Path("pipeline/adversarial_edit.py").read_text(encoding="utf-8")
        self.assertNotIn("max_tokens=8000", src)


class NarratorLockReportingTest(unittest.TestCase):
    """The inactive-lock record must be first-person-only and survive a resume."""

    def test_a_third_person_book_is_never_reported(self):
        # The eval writes the flag for first_person only, so a third-person log
        # has no key at all. `first_person=True` means "treat an absent flag as
        # a gap", so the CALLER must not ask that question about a third-person
        # book — drafting now gates it on the genre. The bare call is the safe
        # one and is what a third-person path should use.
        from pipeline.phases.common import narrator_lock_was_inactive
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "eval.json"
            p.write_text(json.dumps({"overall_score": 7.1}), encoding="utf-8")
            self.assertFalse(narrator_lock_was_inactive(str(p)))

    def test_a_first_person_gap_is_still_reported(self):
        from pipeline.phases.common import narrator_lock_was_inactive
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "eval.json"
            p.write_text(json.dumps({"narrator_violations": []}), encoding="utf-8")
            self.assertTrue(narrator_lock_was_inactive(str(p), first_person=True))

    def test_drafting_gates_the_check_on_perspective(self):
        import inspect
        from pipeline.phases import drafting
        src = inspect.getsource(drafting)
        self.assertIn('_is_first_person = load_genre().get("perspective")', src)
        self.assertIn("if (_is_first_person", src)

    def test_the_summary_merges_with_a_prior_record(self):
        # A resumed run drafts only the remaining chapters; overwriting the
        # record would erase the chapters an earlier pass flagged, which is the
        # evidence the record exists to keep.
        import inspect
        from pipeline.phases import drafting
        src = inspect.getsource(drafting)
        self.assertIn('prior = state.get("narrator_lock_inactive_chapters")',
                      src)
        self.assertIn("set(inactive_lock_chapters)", src)


if __name__ == "__main__":
    unittest.main()
