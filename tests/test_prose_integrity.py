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
    def test_clean_chapter_is_untouched(self):
        body = "# Chapter 7: Names\n\n" + "A perfectly ordinary paragraph of prose. " * 30
        self.assertIsNone(prose.find_non_prose_start(body))
        self.assertIsNone(prose.cut_reason(body))
        self.assertEqual(body.strip(), prose.strip_non_prose(body))

    def test_short_but_clean_text_is_not_flagged_as_non_prose(self):
        """Length alone is not a non-prose signal; the flag is about surviving prose."""
        body = "# Chapter 1: A\n\nHe walked. She followed. They argued.\n"
        self.assertIsNone(prose.cut_reason(body))
        self.assertTrue(prose.looks_like_non_prose(body))  # too short to be a chapter

    def test_empty_text_is_clean_and_empty(self):
        self.assertEqual("", prose.strip_non_prose(""))
        self.assertIsNone(prose.cut_reason(""))


if __name__ == "__main__":
    unittest.main()
