"""The tag format has one owner, so every caller sees the same tags.

Three regexes used to parse `[Plant: slug - "desc"]`: the validator's, a copy in
`extract_outline_debts`, and a stricter one in gen_outline that required quotes
and forbade hyphens in slugs. A tag one caller accepted could be invisible to
another.
"""

import re
import unittest
from pathlib import Path

from core import outline as outline_mod

ROOT = Path(__file__).resolve().parent.parent
V4_PART1 = ROOT / "projects" / "sir the confortable v4" / ".outline_part1.md"

SHAPES = """### Chapter 1: Open
- [Plant: brass_key - "a brass key under the floorboard"]

### Chapter 4: More
- [Plant: wax_seal: the yellow wax on the decree]
- [Plant: chalk-nub - "a greasy chalk nub"]
- [Harvest: brass_key - "the key turns in the lock"]

### Chapter 9: Later
- [Harvest: wax_seal - "the seal burns through the decree"]
"""


class TagParsingTest(unittest.TestCase):
    def test_every_tag_shape_is_found(self):
        plants, harvests = outline_mod.parse_plant_tags(SHAPES)
        self.assertEqual({"brass_key", "wax_seal", "chalk-nub"},
                         {p["slug"] for p in plants})
        self.assertEqual({"brass_key", "wax_seal"}, {h["slug"] for h in harvests})

    def test_the_strict_shapes_are_no_longer_missed(self):
        """A colon separator and a hyphenated slug both used to be invisible."""
        plants, _ = outline_mod.parse_plant_tags(SHAPES)
        descs = {p["slug"]: p["desc"] for p in plants}
        self.assertEqual("the yellow wax on the decree", descs["wax_seal"])
        self.assertEqual("a greasy chalk nub", descs["chalk-nub"])

    def test_chapters_are_attributed(self):
        plants, harvests = outline_mod.parse_plant_tags(SHAPES)
        by_slug = {p["slug"]: p["chapter"] for p in plants}
        self.assertEqual({"brass_key": 1, "wax_seal": 4, "chalk-nub": 4}, by_slug)
        self.assertEqual({"brass_key": 4, "wax_seal": 9},
                         {h["slug"]: h["chapter"] for h in harvests})

    def test_sections_split_on_every_heading_dress(self):
        text = "### Chapter 1: A\nx\n### Ch 2: B\ny\n### CHAPTER 3: C\nz\n"
        self.assertEqual([1, 2, 3], sorted(outline_mod.chapter_sections(text)))

    def test_a_wrapping_quote_pair_is_removed_but_apostrophes_survive(self):
        text = ("### Chapter 1: A\n"
                "- [Plant: poss - \"the generals' plan\"]\n"
                "- [Plant: own - \"'Tis the season\"]\n"
                "- [Plant: bare - the plain form]\n")
        descs = {p["slug"]: p["desc"] for p in outline_mod.parse_plant_tags(text)[0]}
        self.assertEqual("the generals' plan", descs["poss"])
        self.assertEqual("'tis the season", descs["own"])
        self.assertEqual("the plain form", descs["bare"])

    def test_no_tags_is_not_an_error(self):
        self.assertEqual(([], []), outline_mod.parse_plant_tags("### Chapter 1: A\nprose\n"))


class CallersAgreeTest(unittest.TestCase):
    """The whole point: the validator and the debt extractor read the same tags."""

    def test_debts_are_exactly_the_plants_the_validator_can_match(self):
        plants, harvests = outline_mod.parse_plant_tags(SHAPES)
        harvested = {h["slug"] for h in harvests}
        expected = {p["slug"] for p in plants if p["slug"] not in harvested}
        debts = outline_mod.extract_outline_debts(SHAPES)
        self.assertEqual(expected, {d.split("Setup: ")[1].split(" - ")[0] for d in debts})
        self.assertEqual({"chalk-nub"}, expected)

    def test_validator_reports_the_dangling_harvest_using_the_same_tags(self):
        text = SHAPES + "\n### Chapter 12: X\n- [Harvest: never_planted - \"nothing set this up\"]\n"
        ok, err = outline_mod.validate_plants_harvests(text)
        self.assertFalse(ok)
        self.assertIn("never_planted", err)

    def test_same_chapter_harvest_is_an_order_error(self):
        text = ("### Chapter 5: A\n"
                "- [Plant: same_ch - \"set up\"]\n"
                "- [Harvest: same_ch - \"paid off\"]\n")
        ok, err = outline_mod.validate_plants_harvests(text)
        self.assertFalse(ok)
        self.assertIn("Order error", err)


class DebtDeliveryTest(unittest.TestCase):
    """A debt must be able to reach the drafter.

    The old consumer matched a chapter's *harvest* slugs against the debt
    strings. A debt is by construction a plant with no harvest anywhere, so the
    two can never be equal — the guardrail had never fired.
    """

    DEBTS = ['Ch 1 Setup: fear_legitimacy - "Baal II loses long-term trust"',
             'Ch 7 Setup: morra_infiltration - "Morra gets inside the keep"',
             'Ch 20 Setup: late_plant - "declared after where we are"',
             "not a debt at all"]

    def test_only_setups_from_earlier_chapters_are_offered(self):
        got = outline_mod.open_debts_for_chapter(self.DEBTS, 12)
        self.assertEqual(["fear_legitimacy", "morra_infiltration"],
                         [d["slug"] for d in got])

    def test_a_debt_can_reach_a_chapter_that_has_no_harvest_of_it(self):
        """The whole point: an unscheduled setup is still visible."""
        got = outline_mod.open_debts_for_chapter(self.DEBTS, 12)
        self.assertTrue(got)
        self.assertNotIn("late_plant", [d["slug"] for d in got])

    def test_unparseable_entries_are_skipped(self):
        got = outline_mod.open_debts_for_chapter(self.DEBTS + ["", "garbage"], 12)
        self.assertTrue(all(d["slug"] for d in got))

    def test_oldest_first_and_capped(self):
        debts = [f'Ch {n} Setup: s{n} - "d{n}"' for n in (9, 3, 6, 1, 8)]
        got = outline_mod.open_debts_for_chapter(debts, 12, limit=3)
        self.assertEqual([1, 3, 6], [d["chapter"] for d in got])

    def test_no_debts_is_empty_not_an_error(self):
        self.assertEqual([], outline_mod.open_debts_for_chapter([], 5))
        self.assertEqual([], outline_mod.open_debts_for_chapter(None, 5))

    def test_parse_debt_round_trips(self):
        d = outline_mod.parse_debt('Ch 3 Setup: a_slug - "some description"')
        self.assertEqual({"chapter": 3, "slug": "a_slug",
                          "desc": "some description"}, d)
        self.assertIsNone(outline_mod.parse_debt("Ch 3 Setup: nope"))


@unittest.skipUnless(V4_PART1.is_file(), "v4 outline not present")
class RealOutlineTest(unittest.TestCase):
    def test_the_real_outline_parses_both_sides(self):
        plants, harvests = outline_mod.parse_plant_tags(
            V4_PART1.read_text(encoding="utf-8"))
        self.assertGreater(len(plants), 20)
        self.assertGreater(len(harvests), 20)
        # Every plant is attributed to a real chapter.
        self.assertTrue(all(p["chapter"] >= 1 for p in plants))

    def test_tags_with_apostrophes_are_the_majority_of_what_was_lost(self):
        """The old `[^'\\"\\]]+` class dropped any description with a possessive."""
        text = V4_PART1.read_text(encoding="utf-8")
        plants, harvests = outline_mod.parse_plant_tags(text)
        raw_plants = len(re.findall(r"\[Plant:", text))
        raw_harvests = len(re.findall(r"\[Harvest:", text))
        self.assertEqual(raw_plants, len(plants))
        self.assertEqual(raw_harvests, len(harvests))
        self.assertGreater(len(plants), 30)

    def test_the_validator_fires_on_the_real_outline(self):
        """It reports real problems that are currently only warned about."""
        ok, err = outline_mod.validate_plants_harvests(
            V4_PART1.read_text(encoding="utf-8"))
        self.assertFalse(ok)
        self.assertTrue("Dangling harvest" in err or "Order error" in err)


if __name__ == "__main__":
    unittest.main()
