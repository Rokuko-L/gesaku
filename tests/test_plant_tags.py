"""The tag format has one owner, so every caller sees the same tags.

Three regexes used to parse `[Plant: slug - "desc"]`: the validator's, a copy in
`extract_outline_debts`, and a stricter one in gen_outline that required quotes
and forbade hyphens in slugs. A tag one caller accepted could be invisible to
another.
"""

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
        self.assertEqual([0, 1, 2, 3], sorted(outline_mod.chapter_sections(text)))

    def test_preamble_tags_are_not_dropped(self):
        """The global thread ledger sits before Chapter 1 and used to vanish."""
        text = ('# Outline\n\n- [Plant: global_thread - "declared up front"]\n\n'
                "### Chapter 1: A\n- [Harvest: global_thread - \"paid off\"]\n")
        plants, harvests = outline_mod.parse_plant_tags(text)
        self.assertEqual(["global_thread"], [p["slug"] for p in plants])
        self.assertEqual(0, plants[0]["chapter"])
        ok, err = outline_mod.validate_plants_harvests(text)
        self.assertTrue(ok, err)

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


@unittest.skipUnless(V4_PART1.is_file(), "v4 outline not present")
class RealOutlineTest(unittest.TestCase):
    def test_the_real_outline_parses_both_sides(self):
        plants, harvests = outline_mod.parse_plant_tags(
            V4_PART1.read_text(encoding="utf-8"))
        self.assertGreater(len(plants), 20)
        self.assertGreater(len(harvests), 20)
        # Every plant is attributed to a real chapter.
        self.assertTrue(all(p["chapter"] >= 1 for p in plants))

    def test_the_validator_fires_on_the_real_outline(self):
        """It reports real problems that are currently only warned about."""
        ok, err = outline_mod.validate_plants_harvests(
            V4_PART1.read_text(encoding="utf-8"))
        self.assertFalse(ok)
        self.assertTrue("Dangling harvest" in err or "Order error" in err)


if __name__ == "__main__":
    unittest.main()
