"""The hygiene gate must fail on real gaps, not on words that are not characters.

v4's plant_hygiene.json reported coverage pass:false because the required list
was ['Baal','Hiro','I','Law','Lily','Mira','T'] — and 'Law' never appears in the
pre-reveal outline (it is a force, "The Law"). The gate was right to fail, on a
name that should never have been required.
"""

import re
import unittest
from pathlib import Path

from core import canon as canon_mod
from core import plant_hygiene as ph

ROOT = Path(__file__).resolve().parent.parent
V4 = ROOT / "projects" / "sir the confortable v4"

REGISTRY = """## Characters

### 1. **Corvo Quill**
* **Role:** Vengeful investigator. Watches Lily, Mira and Hiro for signs of Baal.
* **Conflict:** **The Law** protects her absolutely.
* **Background:** Trained by the Viking Archivist of the north.
"""
CANON = """## Foundation

- visible_from=8: Lily Bakersville's soul is Baal, the T-5 Abyssal Demon Lord.
- visible_from=12: Baal lost his memories for the first 5 years.
"""


def _canon_with(fact_lines: str) -> canon_mod.ParsedCanon:
    """Facts live under `## Foundation` as `- visible_from=N: fact`."""
    return canon_mod.parse_canon("## Foundation\n\n" + fact_lines)


class NameDerivationTest(unittest.TestCase):
    def test_the_pronoun_i_is_never_a_character(self):
        parsed = _canon_with(
            "- visible_from=2: A and B are twins, and I watched them.\n")
        self.assertEqual([], canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_power_tier_tokens_are_not_characters(self):
        parsed = _canon_with(
            "- visible_from=2: Mira is T-1 Radiant, Lily is T-5 Abyssal.\n")
        self.assertEqual(["Lily", "Mira"],
                         canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_single_letter_names_still_work(self):
        """Deliberate support: short fiction uses A and B as character names."""
        parsed = _canon_with("- visible_from=2: A trusts B with a secret.\n")
        self.assertEqual(["A", "B"],
                         canon_mod.action_plant_characters(parsed, "A and B are twins."))

    def test_a_real_character_named_in_the_sealed_facts_is_kept(self):
        parsed = _canon_with(
            "- visible_from=5: Mira carries Baal's soul-light into Lily's body.\n")
        self.assertEqual(["Baal", "Lily", "Mira"],
                         canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_substring_only_matches_are_rejected(self):
        """`Vik` is inside 'Viking' — the old substring test admitted it."""
        parsed = _canon_with("- visible_from=3: Vik reports to nobody.\n")
        self.assertEqual([], canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_canon_terminology_is_not_a_character(self):
        parsed = _canon_with(
            "- visible_from=4: Law III is irreversible, and Law I filters intent.\n")
        self.assertEqual([], canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_article_led_and_enumerated_both_count_as_terms(self):
        parsed = _canon_with(
            "- visible_from=6: The Veil Protocol blocks resonance.\n")
        self.assertEqual([], canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_a_lowercase_homograph_does_not_rescue_a_term(self):
        """'kingdom law classifies her' is the common noun, not the term `Law`."""
        parsed = _canon_with(
            "- visible_from=4: Law III is irreversible; kingdom law classifies her as T-5.\n")
        self.assertEqual([], canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_a_name_used_normally_is_not_a_term(self):
        """One plain mention is enough to make it a character."""
        parsed = _canon_with(
            "- visible_from=8: Baal inherits the title from his father Baal I.\n")
        self.assertIn("Baal", canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_a_word_merely_ending_in_the_does_not_make_a_term(self):
        """`endswith("the")` also matched scythe / breathe / lithe."""
        parsed = _canon_with(
            "- visible_from=6: She hefts the scythe Mira forged.\n")
        self.assertIn("Mira", canon_mod.action_plant_characters(parsed, REGISTRY))

    def test_a_real_article_still_marks_a_term(self):
        parsed = _canon_with(
            "- visible_from=6: The Veil Protocol blocks every attempt.\n")
        self.assertNotIn("Veil", canon_mod.action_plant_characters(parsed, REGISTRY))


@unittest.skipUnless((V4 / "canon.md").is_file(), "v4 not present")
class RealProjectTest(unittest.TestCase):
    """The observed failure, on the real files."""

    def _required(self):
        parsed = canon_mod.parse_canon((V4 / "canon.md").read_text(encoding="utf-8"))
        chars = (V4 / "characters.md").read_text(encoding="utf-8")
        return parsed, canon_mod.action_plant_characters(parsed, chars)

    def test_junk_names_are_gone(self):
        _, required = self._required()
        self.assertNotIn("I", required)
        self.assertNotIn("T", required)
        self.assertNotIn("Law", required)
        for name in required:
            self.assertGreaterEqual(len(name), 3)

    def test_the_real_cast_is_still_required(self):
        _, required = self._required()
        for name in ("Baal", "Hiro", "Lily", "Mira"):
            self.assertIn(name, required)

    def test_the_floor_stays_at_one_and_v4_passes(self):
        """Data said do NOT raise the floor: >=2 fails legitimately rare names."""
        parsed, required = self._required()
        proj = (ROOT / "projects" / "sir the confortable v4")
        outline = (proj / ".outline_part1.md").read_text(encoding="utf-8")
        cov = ph.action_plant_coverage(outline, required, parsed.reveal_chapter())
        counts = {n: len(c) for n, c in cov["per_character"].items()}
        for name, n in counts.items():
            self.assertGreaterEqual(n, 1, f"{name} absent from every pre-reveal chapter")
        # And the gate would still catch a total blackout. (NB not "Nobody":
        # v4 genuinely has characters called the Nobody Kids.)
        self.assertFalse(
            ph.action_plant_coverage(outline, ["Zzzquux"], parsed.reveal_chapter())["pass"])


if __name__ == "__main__":
    unittest.main()
