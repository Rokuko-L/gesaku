"""Craft-guard locks for the v4/v5 failure modes.

Covers the MC-locked narrator (v4 ch19 "I am Mira Bakersville"), the
cross-chapter refrain ban (v5 "the plan can wait"), and the draft length
band (v5 5.6k-word ch19). Prose-guard cases live in test_prose_integrity.py
with the real fixtures.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.genre import perspective_eval_rule, perspective_system_block
from core.outline import focus_names, protagonist_aliases
from core.prose import narrator_identity_swaps
from pipeline.draft_chapter import scan_prior_chapter_crutches
from pipeline.pipeline_infra import chapter_length_bounds


OUTLINE = """# Demo
## DETAILED CHAPTER OUTLINES

### Chapter 1: Torch a Village
**Focus:** Lily Bakersville (Baal)
**MC presence:** on-page

### Chapter 2: Bath Time
**POV:** Lily Bakersville (Baal)

### Chapter 19: Joint Shield
**Focus:** Mira Bakersville
**MC presence:** on-page
"""

# The generator template and real project outlines dress Focus differently.
OUTLINE_TEMPLATE_STYLE = """### Chapter 1: Torch
1. Focus: Lily Bakersville (Baal)
### Chapter 2: Bath
- **Focus:** **Lily** Bakersville (Baal)
### Chapter 3: Rival
- Focus: Lily
"""


class FocusVsNarratorTest(unittest.TestCase):
    """Focus may move; the first-person 'I' may not."""

    def test_focus_labels_parse_bold_and_plain(self):
        self.assertEqual(
            focus_names(OUTLINE),
            ["Lily Bakersville (Baal)", "Lily Bakersville (Baal)", "Mira Bakersville"],
        )

    def test_focus_labels_parse_generator_template_and_bullets(self):
        # B1: `1. Focus:` and `- **Focus:** **Lily**` must parse.
        names = focus_names(OUTLINE_TEMPLATE_STYLE)
        self.assertEqual(len(names), 3, names)
        self.assertTrue(any("Lily" in n for n in names), names)

    def test_mc_aliases_include_secret_name(self):
        aliases = protagonist_aliases(OUTLINE)
        self.assertIn("lily", aliases)
        self.assertIn("baal", aliases)
        self.assertNotIn("mira", aliases)

    def test_template_style_aliases_still_name_the_mc(self):
        aliases = protagonist_aliases(OUTLINE_TEMPLATE_STYLE)
        self.assertIn("lily", aliases)
        self.assertIn("baal", aliases)
        self.assertNotIn("**lily", aliases)

    def test_rival_parentheticals_do_not_launder_aliases(self):
        # Compacter bug: Focus: Mira (the Render of Ash) must not whitelist
        # "render"/"ash" for everyone.
        text = "**Focus:** Lily Bakersville (Baal)\n**Focus:** Mira Bakersville (the Render of Ash)\n"
        aliases = protagonist_aliases(text)
        self.assertIn("lily", aliases)
        self.assertIn("baal", aliases)
        self.assertNotIn("render", aliases)
        self.assertNotIn("mira", aliases)

    def test_first_person_block_is_mc_locked(self):
        block = perspective_system_block("first_person")
        self.assertIn("MC-LOCKED", block)
        self.assertIn("THIRD-PERSON INTERLUDE", block)
        self.assertIn("hard scene break", block)
        self.assertIn("Never write a side character", block)

    def test_v4_mira_head_hop_is_flagged(self):
        allowed = protagonist_aliases(OUTLINE)
        v4_ch19 = "I am Mira Bakersville and I am looking at my daughter's body."
        self.assertEqual(
            narrator_identity_swaps(v4_ch19, allowed),
            ["I am Mira Bakersville"],
        )

    def test_dialogue_self_intro_is_not_a_narrator_swap(self):
        allowed = protagonist_aliases(OUTLINE)
        spoken = '"I am Marbas, your general," the man said.'
        self.assertEqual(narrator_identity_swaps(spoken, allowed), [])
        self.assertEqual(
            narrator_identity_swaps("He said 'I am Mira Bakersville' and left.", allowed),
            [],
        )
        self.assertEqual(
            narrator_identity_swaps("He said “I am Mira Bakersville” and left.", allowed),
            [],
        )

    def test_stray_quote_does_not_swallow_the_line(self):
        allowed = protagonist_aliases(OUTLINE)
        self.assertTrue(
            narrator_identity_swaps('The board was 5" thick and I am Mira Bakersville.', allowed)
        )
        self.assertTrue(
            narrator_identity_swaps('She said "hello. I am Mira Bakersville.', allowed)
        )

    def test_unicode_and_hyphen_names(self):
        allowed = protagonist_aliases(OUTLINE)
        self.assertTrue(narrator_identity_swaps("I am Łukasz Nowak.", allowed))
        self.assertTrue(narrator_identity_swaps("I am Élise Moreau.", allowed))
        self.assertTrue(narrator_identity_swaps("I am Lily-Ann the Destroyer.", allowed))
        self.assertTrue(narrator_identity_swaps("I am Kael-Lily.", allowed))

    def test_narrator_lock_blocks_force_keep(self):
        # B-1: a score tax is not a keep-gate.
        from pipeline.phases.common import narrator_lock_blocks
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "eval.json"
            self.assertFalse(narrator_lock_blocks(str(p)))  # missing
            p.write_text(json.dumps({"narrator_violations": []}), encoding="utf-8")
            self.assertFalse(narrator_lock_blocks(str(p)))
            p.write_text(json.dumps({"narrator_violations": ["I am Mira"]}), encoding="utf-8")
            self.assertTrue(narrator_lock_blocks(str(p)))

    def test_possessives_and_contractions_are_not_swaps(self):
        allowed = protagonist_aliases(OUTLINE)
        self.assertEqual(narrator_identity_swaps("I am Lily's mother now.", allowed), [])
        self.assertEqual(narrator_identity_swaps("I am Baal's heir.", allowed), [])
        self.assertEqual(narrator_identity_swaps("I'm Lily and I hate baths.", allowed), [])

    def test_unknown_mc_does_not_flag_the_protagonist(self):
        # B1 consequence: empty aliases must not punish every self-id.
        self.assertEqual(narrator_identity_swaps("I am Lily Bakersville.", set()), [])
        self.assertEqual(narrator_identity_swaps("I am Lily Bakersville.", None), [])

    def test_mc_and_alias_are_allowed(self):
        allowed = protagonist_aliases(OUTLINE)
        self.assertEqual(
            narrator_identity_swaps("I am Lily Bakersville, five years old.", allowed),
            [],
        )
        self.assertEqual(
            narrator_identity_swaps("I am Baal the Second.", allowed),
            [],
        )

    def test_age_and_title_are_not_identity_swaps(self):
        allowed = protagonist_aliases(OUTLINE)
        self.assertEqual(narrator_identity_swaps("I am seventeen.", allowed), [])
        self.assertEqual(narrator_identity_swaps("I am so tired.", allowed), [])

    def test_eval_rule_names_the_hard_failure(self):
        rule = perspective_eval_rule("first_person")
        self.assertIn("hard failure", rule)
        self.assertIn("interlude", rule)


class RefrainBanE2ETest(unittest.TestCase):
    def test_v5_plan_can_wait_is_banned(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for i in range(1, 13):
                if i in (1, 3, 7, 11):
                    body = "*The plan can wait,* he thought.\n"
                else:
                    body = f"Chapter {i} carried a unique incident about clay and rope.\n"
                (d / f"ch_{i:02d}.md").write_text(
                    f"# Chapter {i}\n\n{body}", encoding="utf-8"
                )
            keys = [p for p, _ in scan_prior_chapter_crutches(d, 13)]
            self.assertTrue(any("plan can wait" in p for p in keys), keys)

    def test_proper_nouns_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for i in range(1, 13):
                (d / f"ch_{i:02d}.md").write_text(
                    f"# Chapter {i}\n\nThe Ashen Field was quiet. Nanny Buttercup cooed.\n",
                    encoding="utf-8",
                )
            keys = [p for p, _ in scan_prior_chapter_crutches(d, 13)]
            self.assertFalse(any("ashen field" in p for p in keys), keys)
            self.assertFalse(any("buttercup" in p for p in keys), keys)


class LengthBandE2ETest(unittest.TestCase):
    def test_v5_oversized_ch19_would_be_rejected(self):
        # v5 ch19 shipped at 5637 words against a ~3695 target (1.52x).
        min_w, max_w = chapter_length_bounds(3695)
        self.assertLess(min_w, 3000)
        self.assertGreater(5637, max_w)
        self.assertLess(3000, max_w)

    def test_climax_gets_a_higher_ceiling(self):
        _, normal = chapter_length_bounds(3200, is_climax=False)
        _, climax = chapter_length_bounds(3200, is_climax=True)
        self.assertGreater(climax, normal)
        self.assertGreaterEqual(chapter_length_bounds(3200)[0], 1500)

    def test_climax_flag_is_wired_from_chapter_position(self):
        from pipeline.pipeline_infra import is_climax_chapter
        self.assertTrue(is_climax_chapter(24, 24, "they fight"))
        self.assertTrue(is_climax_chapter(12, 24, "the final coup at dawn"))
        self.assertFalse(is_climax_chapter(3, 24, "bath time"))

    def test_zero_target_does_not_explode(self):
        self.assertEqual(chapter_length_bounds(0), (1, 2))


if __name__ == "__main__":
    unittest.main()
