"""Offline tests for core.canon — sealed foundation and chapter-scoped views.

Run: uv run python -m unittest scratch.test_canon_scoping
"""

import unittest

from core.canon import (
    FoundationFact,
    action_plant_characters,
    disclosure_md,
    judge_view_md,
    parse_canon,
    public_foundation_md,
    sealed_denylist_terms,
    sealed_foundation_md,
    writer_view_md,
)


SAMPLE = """## Foundation (background truth, not yet revealed to readers)

- visible_from=1: The capital sits on a salted plain.
- visible_from=14: B is the architect of the succession plot; A is the public face.
- A river runs under the palace.

## Core Canon

- The king is dead.

## As of Chapter 1

- A arrived at the gate.

## As of Chapter 3

- A found the sealed letter.

## Revision Sync

### Core

- (revision-only fact)
"""


class ParseCanonTest(unittest.TestCase):
    def test_parse_layers(self):
        p = parse_canon(SAMPLE)
        self.assertEqual(len(p.foundation_facts), 3)
        self.assertEqual(p.foundation_facts[0].visible_from, 1)
        self.assertEqual(p.foundation_facts[1].visible_from, 14)
        self.assertEqual(p.foundation_facts[2].visible_from, 1)  # untagged default
        self.assertEqual(p.core_facts, ["The king is dead."])
        self.assertEqual(sorted(p.as_of.keys()), [1, 3])
        self.assertIn("Revision", p.revision_sync + "Revision")

    def test_reveal_chapter(self):
        p = parse_canon(SAMPLE)
        self.assertEqual(p.reveal_chapter(), 14)

    def test_empty(self):
        p = parse_canon("")
        self.assertEqual(p.foundation_facts, [])
        self.assertIsNone(p.reveal_chapter())
        self.assertEqual(writer_view_md(p, 5), "")


class ViewsTest(unittest.TestCase):
    def setUp(self):
        self.p = parse_canon(SAMPLE)

    def test_public_foundation_excludes_sealed(self):
        md = public_foundation_md(self.p, 1)
        self.assertIn("salted plain", md)
        self.assertNotIn("architect", md)
        self.assertIn("river runs under", md)  # untagged = public

    def test_public_foundation_unlocks_at_reveal(self):
        md = public_foundation_md(self.p, 14)
        self.assertIn("architect", md)

    def test_sealed_foundation_md(self):
        md = sealed_foundation_md(self.p)
        self.assertIn("visible_from=14", md)
        self.assertIn("architect", md)

    def test_disclosure_prior_only(self):
        self.assertNotIn("sealed letter", disclosure_md(self.p, 1))
        self.assertIn("sealed letter", disclosure_md(self.p, 4))
        self.assertNotIn("sealed letter", disclosure_md(self.p, 3))  # strict <

    def test_writer_view_ch1_no_leak(self):
        md = writer_view_md(self.p, 1)
        self.assertIn("salted plain", md)
        self.assertIn("The king is dead", md)
        # As-of Chapter 1 is current-chapter knowledge — not prior disclosure.
        self.assertNotIn("A arrived at the gate", md)
        self.assertNotIn("architect", md)
        self.assertNotIn("sealed letter", md)
        self.assertNotIn("Revision", md)
        self.assertNotIn("revision-only", md)

    def test_writer_view_ch2_includes_prior_as_of(self):
        md = writer_view_md(self.p, 2)
        self.assertIn("A arrived at the gate", md)
        self.assertNotIn("sealed letter", md)
        self.assertNotIn("architect", md)

    def test_writer_view_at_reveal_unlocks_sealed(self):
        md = writer_view_md(self.p, 14)
        self.assertIn("architect", md)
        self.assertIn("sealed letter", md)

    def test_judge_view_matches_writer(self):
        self.assertEqual(judge_view_md(self.p, 2), writer_view_md(self.p, 2))
        self.assertNotIn("architect", judge_view_md(self.p, 2))


class DenylistTest(unittest.TestCase):
    def test_sealed_terms_included(self):
        p = parse_canon(SAMPLE)
        terms = sealed_denylist_terms(p)
        self.assertIn("architect", terms)
        self.assertIn("secretly", terms)  # always-on frame
        # Public fact words are not the point of the denylist beyond sealed content
        self.assertNotIn("salted", terms)

    def test_meaning_frames_always_present(self):
        p = parse_canon("- visible_from=2: something mild")
        terms = sealed_denylist_terms(p)
        self.assertIn("the real", terms)
        self.assertIn("true identity", terms)


class ActionPlantCharactersTest(unittest.TestCase):
    def test_sealed_names_in_registry(self):
        p = parse_canon(
            "## Foundation\n\n- visible_from=14: Mira is the architect; A is the face.\n"
        )
        chars = "# Characters\n\n## Mira\n\nAdvisor.\n\n## A\n\nGuard.\n"
        names = action_plant_characters(p, chars)
        self.assertIn("Mira", names)
        self.assertIn("A", names)

    def test_empty_without_registry(self):
        p = parse_canon("## Foundation\n\n- visible_from=14: Mira is the architect.\n")
        self.assertEqual(action_plant_characters(p, ""), [])


class TagVariantsTest(unittest.TestCase):
    def test_bracket_and_paren_forms(self):
        text = (
            "## Foundation\n\n"
            "- [visible_from: 9] bracket form\n"
            "- (from 7) paren form\n"
            "- vf=3: short form\n"
            "- visible_from 4: space form\n"
        )
        p = parse_canon(text)
        by_fact = {f.fact: f.visible_from for f in p.foundation_facts}
        self.assertEqual(by_fact["bracket form"], 9)
        self.assertEqual(by_fact["paren form"], 7)
        self.assertEqual(by_fact["short form"], 3)
        self.assertEqual(by_fact["space form"], 4)
        self.assertEqual(p.reveal_chapter(), 3)
        self.assertEqual(p.malformed_visible_from, [])


class FailClosedMalformedTagTest(unittest.TestCase):
    def test_typo_tag_is_sealed_not_public(self):
        text = "## Foundation\n\n- visible_from fourteen: the true heir of B.\n"
        p = parse_canon(text)
        self.assertEqual(len(p.foundation_facts), 1)
        fact = p.foundation_facts[0]
        self.assertTrue(fact.malformed_tag)
        self.assertGreater(fact.visible_from, 1)
        self.assertNotIn("true heir", public_foundation_md(p, 1))
        self.assertNotIn("true heir", writer_view_md(p, 24))
        self.assertIn("true heir", sealed_foundation_md(p))

    def test_malformed_does_not_invent_reveal_chapter(self):
        text = (
            "## Foundation\n\n"
            "- visible_from=12: real twist\n"
            "- visible_from oops: typo seal\n"
        )
        p = parse_canon(text)
        self.assertEqual(p.reveal_chapter(), 12)
        self.assertEqual(len(p.malformed_visible_from), 1)

    def test_prose_from_is_not_mistaken_for_tag(self):
        text = "## Foundation\n\n- From the capital, the salt road runs east.\n"
        p = parse_canon(text)
        self.assertEqual(p.foundation_facts[0].visible_from, 1)
        self.assertFalse(p.foundation_facts[0].malformed_tag)
        self.assertEqual(p.malformed_visible_from, [])

    def test_missing_number_is_sealed(self):
        text = "## Foundation\n\n- visible_from=: the mask of A.\n"
        p = parse_canon(text)
        self.assertTrue(p.foundation_facts[0].malformed_tag)
        self.assertGreater(p.foundation_facts[0].visible_from, 1)


if __name__ == "__main__":
    unittest.main()
