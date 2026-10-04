"""The prose guard: model output that is not chapter prose must not ship.

Fixtures are taken from real corruption found in
``projects/sir the confortable v4`` (a reasoning dump mid-chapter, and notes
blocks appended after six chapters).
"""

import unittest

from core import prose

# The real derail: ch_20 interrupts itself mid-sentence and never recovers.
CH20_DERAIL = """# Chapter 20: Ashpaw, You Lumbering One-Horned Ox

The mold ceiling drips something wet and brown onto Lily's cheek, and she doesn't have lungs enough to care. Floating. Always floating.

*Why is the sausage vendor here.* That's her first coherent thought, the one that survives the nothing because it's so stupid it loops. *Marbas. Sausages. What is he doing here.*

She tries to look down at herself, but there's only the upward angle of mold and the way Marbas's hornet's shadow lengthens across the stone. He's holding a sausage like it's a dagger, and he's staring at the Spawn like it's nest. the question says: "What is the role of differential reinforcement in shaping behavior?" It's a single choice, single answer question. The answer should be one of the letters. I have to choose the correct option.

Let me recall what differential reinforcement is. In operant conditioning, differential reinforcement is a procedure where reinforcement is delivered contingent upon the occurrence of a specific behavior.
"""

CLEAN_INTERIORITY = """# Chapter 5: The Burrito

I lay in the burrito with my collarbones prickling under the blanket and my fangs sharp against my tongue.

*Why is the sausage vendor here.* That's her first coherent thought. Let me recall what Mira said about the laundry. She said it twice, and both times she was lying, which is the only honest thing about her.

The door eased shut with a soft click. Her shadow left the crack of light.
""" * 6
# NB the repetition is deliberate: it repeats a weak marker ("Let me recall")
# across a full-length chapter, which must NOT be treated as a derail. Dense
# clustering is the signal, not mere recurrence.

# The four dresses the heading actually turns up in, all seen in v4.
NOTES_TRAILERS = [
    "\n---\n**Revision Notes**\n\n**1. Kept the Three Strongest Sentences:** Retained verbatim.\n",
    "\n---\n\n### Revision Notes\n\n**1. Momentum / Pacing - addressed panel flags:**\nCut Sunsday tourist catalog.\n",
    "\n## Revision Notes\n\n**1. Kept strengths verbatim:** Preserved the three flagged sentences.\n",
    "\n---\n**REVISION NOTES:**\n**1.** Rebuilt the summary into five beats.\n",
]


class DerailDetectionTest(unittest.TestCase):
    def test_real_derail_is_cut_and_flagged(self):
        self.assertEqual("derail", prose.cut_reason(CH20_DERAIL))
        clean = prose.strip_non_prose(CH20_DERAIL)
        self.assertNotIn("differential reinforcement", clean)
        self.assertNotIn("single choice", clean)
        # The prose before the derail survives, ending on a sentence boundary.
        self.assertIn("sausage like it's a dagger", clean)
        self.assertTrue(clean.rstrip().endswith("like it's nest."))

    def test_derail_leaves_a_fragment_and_must_be_redrafted(self):
        self.assertTrue(prose.looks_like_non_prose(CH20_DERAIL))
        self.assertTrue(prose.needs_redraft(CH20_DERAIL))
        self.assertLess(prose.prose_words(CH20_DERAIL), prose.MIN_CHAPTER_WORDS)

    def test_first_person_interiority_is_not_flagged(self):
        """The false-positive guard: this voice is first person by design."""
        self.assertIsNone(prose.cut_reason(CLEAN_INTERIORITY))
        self.assertFalse(prose.looks_like_non_prose(CLEAN_INTERIORITY))
        self.assertEqual(CLEAN_INTERIORITY.strip(), prose.strip_non_prose(CLEAN_INTERIORITY))

    def test_single_weak_marker_is_tolerated(self):
        text = "# Chapter 1: A\n\n" + ("She waited. Let me recall the rest, I thought. " + "word " * 60)
        self.assertIsNone(prose.cut_reason(text))

    def test_clustered_weak_markers_are_cut(self):
        text = (
            "# Chapter 1: A\n\n" + "word " * 40
            + "\n\nLet me recall. Let me think. I should choose."
            + "\n\nmore reasoning follows here\n"
        )
        self.assertEqual("derail", prose.cut_reason(text))
        self.assertNotIn("I should choose", prose.strip_non_prose(text))


class NotesTrailerTest(unittest.TestCase):
    def test_every_heading_dress_is_stripped(self):
        body = "# Chapter 5: The Burrito\n\n" + "Real prose sentence here. " * 40
        for trailer in NOTES_TRAILERS:
            with self.subTest(trailer=trailer.splitlines()[1]):
                self.assertEqual("notes", prose.cut_reason(body + trailer))
                clean = prose.strip_non_prose(body + trailer)
                self.assertNotIn("Revision Notes", clean)
                self.assertNotIn("REVISION NOTES", clean)
                self.assertNotIn("Retained verbatim", clean)
                self.assertTrue(clean.rstrip().endswith("here."))

    def test_notes_alone_do_not_trigger_a_redraft(self):
        body = "# Chapter 5: The Burrito\n\n" + "Real prose sentence here. " * 200
        self.assertFalse(prose.looks_like_non_prose(body + NOTES_TRAILERS[0]))


class CleanTextTest(unittest.TestCase):
    def test_a_bare_notes_line_is_not_a_trailer(self):
        """A chapter may legitimately contain '## Notes' — only a *revision*
        label means the model started explaining itself."""
        body = ("# Chapter 9: A\n\n" + "Real prose sentence here. " * 60
                + "\n\n## Notes\n\nShe kept her own notes in the margins.\n"
                + "More prose after the heading. " * 20)
        self.assertIsNone(prose.cut_reason(body))
        self.assertEqual(body.strip(), prose.strip_non_prose(body))

    def test_a_short_clean_chapter_is_not_sent_to_redraft(self):
        """Length alone is the drafting loop's business, not the guard's."""
        interlude = "# Chapter 9: Interlude\n\nHe walked. She followed. They argued.\n"
        self.assertIsNone(prose.cut_reason(interlude))
        self.assertTrue(prose.looks_like_non_prose(interlude))   # short, so worth flagging
        self.assertFalse(prose.needs_redraft(interlude))          # but not a derail

    def test_a_derailed_fragment_does_need_a_redraft(self):
        self.assertTrue(prose.needs_redraft(CH20_DERAIL))

    def test_a_clean_chapter_is_untouched(self):
        body = "# Chapter 7: Names\n\n" + "A perfectly ordinary paragraph of prose. " * 30
        self.assertIsNone(prose.find_non_prose_start(body))
        self.assertIsNone(prose.cut_reason(body))
        self.assertEqual(body.strip(), prose.strip_non_prose(body))

    def test_empty_text_is_clean_and_empty(self):
        self.assertEqual("", prose.strip_non_prose(""))
        self.assertIsNone(prose.cut_reason(""))


# Real artifacts from v5 (`projects/sir the confortable v5`): the writer
# labelled its own beats and dropped single words in another writing system.
V5_BEAT_HEADINGS = """# Chapter 11: Former Lieutenants Reorganize Without Their Boss

## BEAT 1

The hollow throne room smelled like damp ambition and old resentment.

## BEAT 2

Vexia did not pause for effect.
"""

V5_SCRIPT_SLIPS = (
    "He commanded legions,* Baal's soul thought, seething into the\u6bdb\u5dfe.",
    "The kingdom pretends to care about luminosity measurements while\u5b9e\u9645\u4e0a"
    " trading dowry agreements and arranging marriages.",
    "A skeleton lay in the corner, apparently engaged in a\u957f\u671f argument with a turnip.",
    "Whatever she was looking for had learned to sleep behind the"
    "\u4eea\u8868\u76d8 of her face.",
)


class StripArtifactsTest(unittest.TestCase):
    def test_beat_headings_are_dropped_from_chapter_prose(self):
        clean, removed = prose.strip_artifacts(V5_BEAT_HEADINGS)
        for marker in ("## BEAT 1", "## BEAT 2", "BEAT"):
            self.assertNotIn(marker, clean)
        # The prose around them survives untouched.
        self.assertIn("The hollow throne room smelled like damp ambition", clean)
        self.assertIn("Vexia did not pause for effect.", clean)
        self.assertEqual(2, len(removed))

    def test_every_foreign_script_slip_from_v5_is_removed(self):
        for line in V5_SCRIPT_SLIPS:
            clean, removed = prose.strip_artifacts(line)
            for ch in line:
                if ord(ch) > 0x2FFF and not ch.isascii():
                    self.assertNotIn(ch, clean, f"{ch!r} survived stripping")
            self.assertTrue(removed)

    def test_removal_does_not_orphan_punctuation_or_spaces(self):
        clean, _ = prose.strip_artifacts("He stared into the \u6bdb\u5dfe.")
        self.assertNotIn(" .", clean)
        self.assertNotIn("  ", clean)
        self.assertEqual("He stared into the.", clean)

    def test_every_hole_in_a_multi_slip_line_is_tidied(self):
        """Reverse-order patching must fix each seam, not just the first."""
        clean, _ = prose.strip_artifacts("He met \u6bdb\u5dfe and \u957f\u671f yesterday.")
        self.assertEqual("He met and yesterday.", clean)
        self.assertNotIn("  ", clean)

        clean, _ = prose.strip_artifacts("a \u6bdb\u5dfe b \u957f\u671f c")
        self.assertEqual("a b c", clean)

    def test_a_slip_at_a_line_edge_leaves_no_stray_space(self):
        clean, _ = prose.strip_artifacts("first\n\u6bdb\u5dfe starts the line.\n")
        self.assertEqual("first\nstarts the line.\n", clean)

        clean, _ = prose.strip_artifacts("\u201c\u6bdb\u5dfe is odd\u201d")
        self.assertEqual("\u201cis odd\u201d", clean)

    def test_authorial_unicode_survives(self):
        """Curly quotes, em dashes and Polish names are house style, not slips."""
        body = "“Stop,” said Mikołaj — the émigré — and left. Œuvre."
        clean, removed = prose.strip_artifacts(body)
        self.assertEqual(body, clean)
        self.assertEqual([], removed)

    def test_encoding_damage_is_dropped(self):
        clean, removed = prose.strip_artifacts("clean\ufffd text")
        self.assertEqual("clean text", clean)
        self.assertEqual(1, len(removed))

    def test_a_title_that_is_not_the_first_line_still_survives(self):
        """A draft that opens with the very artifact we strip must still keep
        its title, and must not ship the marker in the title's place."""
        for opener in ("## BEAT 1\n\n", "**Beat 1**\n\n", "Some stray line.\n\n"):
            text = f"{opener}# Chapter 5: The Burrito\n\nHe waited."
            clean, _ = prose.strip_artifacts(text)
            self.assertIn("# Chapter 5: The Burrito", clean, f"lost after {opener!r}")
            self.assertNotIn("BEAT 1", clean)

    def test_markers_above_a_title_are_reported(self):
        text = "## BEAT 1\n\n**Beat 2**\n\n# Chapter 5: T\n\nHe waited."
        clean, removed = prose.strip_artifacts(text)
        self.assertEqual(2, len(removed))
        self.assertIn("# Chapter 5: T", clean)

    def test_a_marker_with_no_title_at_all_is_kept(self):
        """A file whose only line 1 is a marker gets no deletion: for a
        *fresh* draft a heading at the top is indistinguishable from a real
        part divider, and the asymmetry (keep a heading, never delete a title)
        is the whole design. v5's markers were all inside the prose."""
        clean, removed = prose.strip_artifacts("## BEAT 1\n\nHe waited.")
        self.assertIn("## BEAT 1", clean)
        self.assertEqual([], removed)

    def test_a_second_part_heading_survives(self):
        """`# Part II` is a structural heading, not a marker to delete."""
        text = "# Part I: Rise\n\nprose one\n\n# Part II: Fall\n\nprose two\n"
        clean, removed = prose.strip_artifacts(text)
        self.assertIn("# Part I: Rise", clean)
        self.assertIn("# Part II: Fall", clean)
        self.assertEqual([], removed)

    def test_removal_does_not_rewrite_distant_whitespace(self):
        """One stray word must not license a whole-text whitespace pass."""
        text = ("Double  spaces  here.\n\nHe stared into the \u6bdb\u5dfe.\n\n"
                "  An indented  line.\n")
        clean, _ = prose.strip_artifacts(text)
        self.assertIn("Double  spaces  here.", clean)
        self.assertIn("  An indented  line.", clean)
        self.assertNotIn("\u6bdb", clean)
        self.assertNotIn("  .", clean)

    def test_a_title_after_an_epigraph_or_rule_survives(self):
        for prefix in ("Some stray opening line.", "---", "\u2014"):
            text = f"{prefix}\n\n# Chapter 5: The Burrito\n\nHe waited."
            clean, _ = prose.strip_artifacts(text)
            self.assertIn("# Chapter 5: The Burrito", clean, f"lost after {prefix!r}")

    def test_cjk_quotation_brackets_are_not_a_script_slip(self):
        """`projects/NewFakeSaint` quotes dialogue as 「…」; that punctuation is
        not a stray word and must survive."""
        body = "\u300cTrust the blood. It remembers.\u300d"
        clean, removed = prose.strip_artifacts(body)
        self.assertEqual(body, clean)
        self.assertEqual([], removed)

    def test_a_quoted_cjk_word_inside_brackets_is_still_removed(self):
        body = "\u300c\u6bdb\u5dfe\u300d"
        clean, removed = prose.strip_artifacts(body)
        self.assertEqual("\u300c\u300d", clean)
        self.assertEqual(1, len(removed))

    def test_clean_prose_is_untouched(self):
        body = "# Chapter 7: Names\n\nA perfectly ordinary paragraph of prose."
        clean, removed = prose.strip_artifacts(body)
        self.assertEqual(body, clean)
        self.assertEqual([], removed)


class HeadingMarkerTest(unittest.TestCase):
    """A false positive here deletes a paragraph, so these are the guard rails.

    Every "keep" case below is a sentence an earlier version of the pattern
    deleted whole; they live in the suite because the failure is silent.
    """

    KEEP = [
        "Part 2 of my plan was to wait.",
        "Scene 4 was the worst of them all; I still dream about it.",
        "Beat one was the hardest.",
        "Part 2 would have to wait until the nursery was quiet.",
        "Chapter 3 ends with the ledger entry, or so I assumed.",
        "## Part Two: The Fall of the Hollow",
        "# Part II: Fall",
        "He counted the beats: one, two, three.",
        "Scene one, take two, and nobody was watching.",
        "**Beat 3 was the hardest**",
        "**Part 2 was the plan**",
        "**Scene 4 was the worst**",
        "# Beat 3 was the hardest",
    ]
    DROP = [
        "## BEAT 1",
        "## BEAT 4",
        "**Beat 2**",
        "**Beat 3**",
        "BEAT 1",
        "Beat 2",
        "## Scene 3",
        "## BEAT 1 — Morning",
        "## Beat 2: The Morning After",
    ]

    def test_prose_sentences_are_never_headings(self):
        for line in self.KEEP:
            self.assertFalse(prose.looks_like_heading_marker(line),
                             f"{line!r} must not be treated as a heading")

    def test_invented_markers_are_headings(self):
        for line in self.DROP:
            self.assertTrue(prose.looks_like_heading_marker(line),
                            f"{line!r} must be treated as a heading")

    def test_the_first_content_line_always_survives(self):
        """Whatever the opening line says, it is the chapter's title."""
        for title in ("## BEAT 1", "**Beat 2**", "# Chapter 5: Title"):
            text = f"{title}\n\nReal prose follows here."
            clean, _ = prose.strip_artifacts(text)
            self.assertIn(title, clean)

    def test_a_title_after_blank_lines_survives(self):
        text = "\n\n# Chapter 5: Title\n\nProse here."
        clean, _ = prose.strip_artifacts(text)
        self.assertIn("# Chapter 5: Title", clean)

    def test_an_intentional_scene_gap_is_not_collapsed(self):
        """Removing a heading swallows one blank line, not every gap."""
        text = ("# Chapter 1: T\n\nFirst scene.\n\n\n\nSecond scene.\n\n"
                "## BEAT 2\n\nThird scene.\n")
        clean, _ = prose.strip_artifacts(text)
        self.assertIn("First scene.\n\n\n\nSecond scene.", clean)
        self.assertNotIn("## BEAT 2", clean)


if __name__ == "__main__":
    unittest.main()
