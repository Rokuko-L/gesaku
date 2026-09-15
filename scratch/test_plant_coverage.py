"""Offline tests for plant hygiene — leak and under-planting gates.

Run: uv run python -m unittest scratch.test_plant_coverage
"""

import unittest

from core.plant_hygiene import (
    action_plant_coverage,
    check_pre_reveal_leaks,
    extract_chapter_blocks,
    validate_outline_plant_hygiene,
)

OUTLINE_OK = """# TITLE

## DETAILED CHAPTER OUTLINES

### Chapter 1: Gate
1. POV: A
2. Characters: A, Mira, B
5. Scene Beats:
   1. A arrives at the gate. Mira is stacking crates. B watches from the archway without speaking.

### Chapter 2: Letter
1. POV: A
2. Characters: A, Mira, B
5. Scene Beats:
   1. Mira arrives late, leaves a sealed letter on the table, does not explain. B enters, takes the letter, and burns it unread.

### Chapter 14: Reveal
1. POV: A
2. Characters: A, Mira, B
5. Scene Beats:
   1. The mask falls. B is the architect of the succession plot.
"""

OUTLINE_LEAK = """# TITLE

## DETAILED CHAPTER OUTLINES

### Chapter 3: Quiet
1. POV: A
2. Characters: A
5. Scene Beats:
   1. B secretly controls the succession and A does not notice.
"""

OUTLINE_STARVED = """# TITLE

## DETAILED CHAPTER OUTLINES

### Chapter 1: Gate
1. POV: A
2. Characters: A
5. Scene Beats:
   1. A waits alone at the gate.

### Chapter 2: Road
1. POV: A
2. Characters: A
5. Scene Beats:
   1. A rides alone toward the capital.
"""

CANON = """## Foundation

- visible_from=1: The capital sits on a salted plain.
- visible_from=14: B is the architect of the succession plot; A is the public face.
"""

CHARACTERS = "# Characters\n\n## A\n\n## B\n\n## Mira\n"


class ExtractBlocksTest(unittest.TestCase):
    def test_extracts_chapters(self):
        blocks = extract_chapter_blocks(OUTLINE_OK)
        self.assertEqual(sorted(blocks), [1, 2, 14])
        self.assertIn("Mira", blocks[1])


class LeakTest(unittest.TestCase):
    def test_catches_meaning_shaped_beat(self):
        leaks = check_pre_reveal_leaks(
            OUTLINE_LEAK, denylist=["architect", "secretly", "succession"], reveal_chapter=14
        )
        self.assertTrue(leaks)
        self.assertTrue(any("secretly" in p or "succession" in p for p in leaks))

    def test_action_beat_passes(self):
        leaks = check_pre_reveal_leaks(
            OUTLINE_OK, denylist=["architect", "secretly"], reveal_chapter=14
        )
        self.assertEqual(leaks, [])

    def test_no_reveal_no_leaks(self):
        self.assertEqual(check_pre_reveal_leaks(OUTLINE_LEAK, ["secretly"], None), [])


class CoverageTest(unittest.TestCase):
    def test_starved_character_fails(self):
        report = action_plant_coverage(OUTLINE_STARVED, ["B"], 14)
        self.assertFalse(report["pass"])
        self.assertIn("B", report["per_character"])

    def test_present_character_passes(self):
        report = action_plant_coverage(OUTLINE_OK, ["Mira"], 14)
        self.assertTrue(report["pass"])
        self.assertGreaterEqual(len(report["per_character"]["Mira"]), 2)


class FullHygieneTest(unittest.TestCase):
    def test_ok_outline_passes(self):
        ok, err, side = validate_outline_plant_hygiene(OUTLINE_OK, CANON, CHARACTERS)
        self.assertTrue(ok, err)
        self.assertEqual(side["reveal_chapter"], 14)

    def test_leak_fails(self):
        ok, err, side = validate_outline_plant_hygiene(OUTLINE_LEAK, CANON, CHARACTERS)
        self.assertFalse(ok)
        self.assertTrue(side["leaks"])

    def test_starve_fails_when_required_char_missing(self):
        # B is named in sealed canon and appears in characters.md
        ok, err, side = validate_outline_plant_hygiene(OUTLINE_STARVED, CANON, CHARACTERS)
        self.assertFalse(ok)
        self.assertTrue(side["coverage"]["problems"])

    def test_no_sealed_canon_is_noop(self):
        ok, err, side = validate_outline_plant_hygiene(
            OUTLINE_STARVED, "## Foundation\n\n- visible_from=1: public only\n", CHARACTERS
        )
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
