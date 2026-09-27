"""Craft-guard locks for the v4/v5 failure modes.

Covers the MC-locked narrator (v4 ch19 "I am Mira Bakersville"), the
cross-chapter refrain ban (v5 "the plan can wait"), and the draft length
band (v5 5.6k-word ch19). Prose-guard cases live in test_prose_integrity.py
with the real fixtures.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.genre import perspective_eval_rule, perspective_system_block
from core.outline import (
    cast_names_from_outline, focus_names, mc_aliases_for_project,
    protagonist_aliases,
)
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

# Shape of a pre-Focus outline as it actually exists on disk in projects/: no
# Focus/POV field, cast listed on a `**Characters:**` line, entry numbered.
LEGACY_OUTLINE = """# THE DEMON LORD'S GUIDE
## Chapter Outline
### Ch 1: Crib of Contempt
**2568 words**
- **Characters:** Lily (narrator, protagonist), Maledictus, Alistair
- **Emotional arc:** rage to cold calculation
### Ch 2: Can I Cast This
- **Characters:** Lily, Mira, Alistair
### Ch 3: With Crayons
- **Characters:** Mira, Lily
"""

LEGACY_CHARACTERS = """### **1. Princess Liliana "Lily" Celestia Lumengarde (Maledictus)**
Some notes.
### **2. King Alistair, the Hero of Dawn**
More notes.
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

    def test_personification_is_not_a_narrator_swap(self):
        # BLOCKER: each of these was flagged before the cast signal existed.
        # The lock is a keep-gate, so a false positive costs a full
        # discard-and-regenerate cycle — and can skip a chapter outright.
        cast = {"lily", "mira", "alistair", "seraphina"}
        for text in (
            "I am Death, and I am very patient.",
            "I am God,",
            "I am Nobody, and nobody knows me.",
            "I am Trouble with a capital T.",
            "I am Everything you need and more.",
            "I am Famine, and I am everywhere.",
        ):
            self.assertEqual(narrator_identity_swaps(text, {"lily"}, cast), [],
                             text)

    def test_a_casted_name_is_still_a_swap_under_personification_rules(self):
        # The word list must not launder a real character: a book with a
        # character actually named Death still catches the head-hop.
        self.assertTrue(
            narrator_identity_swaps("I am Death.", {"lily"}, {"death"})
        )

    def test_a_relation_to_the_mc_is_not_a_swap(self):
        # "I am Lily's daughter" — the MC is the subject, so it passes.
        cast = {"lily", "mira"}
        self.assertEqual(
            narrator_identity_swaps("I am Lily Bakersville's daughter.",
                                    {"lily", "bakersville"}, cast),
            [],
        )

    def test_a_relation_to_another_character_is_still_a_swap(self):
        # "I am Mira's mother" names Mira as the object, and the narrator is
        # not Mira — that is a head-hop, so it must still be caught.
        cast = {"lily", "mira"}
        self.assertTrue(
            narrator_identity_swaps("I am Mira Bakersville's mother.",
                                    {"lily"}, cast)
        )


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
        self.assertTrue(is_climax_chapter(24, 24, ""))
        self.assertTrue(is_climax_chapter(12, 24, "**Scene type:** Climax"))
        self.assertFalse(is_climax_chapter(3, 24, "bath time"))

    def test_climax_needs_a_label_not_a_prose_word(self):
        # BLOCKER: the old regex matched any chapter whose beats happened to
        # contain climax/battle/final/coup, handing ordinary chapters the
        # 1.55x climax ceiling. A structural fact must come from a label.
        from pipeline.pipeline_infra import is_climax_chapter
        for prose in (
            "1. They fight the final confrontation at dawn.",
            "1. He finally managed to escape the bath.",
            "The climax of her rage arrives without warning.",
            "Scene Stakes: the battle for the throne is won here.",
        ):
            self.assertFalse(is_climax_chapter(3, 24, prose), prose)

    def test_zero_target_does_not_explode(self):
        self.assertEqual(chapter_length_bounds(0), (1, 2))


class LegacyOutlineMCTest(unittest.TestCase):
    """The lock must find the MC on an outline with no Focus field.

    Every first-person project in projects/ predates the Focus field, and its
    outline parses to zero Focus labels. An empty alias set silently disabled
    the narrator lock for all of them, which is how a keep-gate shipped dead.
    """

    def test_legacy_outline_still_names_the_mc(self):
        aliases = protagonist_aliases(LEGACY_OUTLINE)
        self.assertIn("lily", aliases, aliases)

    def test_registry_entry_one_supplies_the_reincarnate_name(self):
        # The MC is "Lily" on the outline but narrates as Maledictus. The
        # registry names both; without it every "I am Maledictus" is a swap.
        aliases = mc_aliases_for_project(LEGACY_OUTLINE, LEGACY_CHARACTERS)
        self.assertIn("lily", aliases, aliases)
        self.assertIn("maledictus", aliases, aliases)
        self.assertNotIn("alistair", aliases, aliases)

    def test_cast_names_come_from_the_characters_line(self):
        cast = cast_names_from_outline(LEGACY_OUTLINE)
        self.assertEqual({"lily", "maledictus", "mira", "alistair"}, cast)

    def test_focus_field_still_wins_over_the_registry(self):
        aliases = mc_aliases_for_project(OUTLINE, LEGACY_CHARACTERS)
        self.assertIn("baal", aliases, aliases)
        self.assertNotIn("maledictus", aliases, aliases)

    def test_a_truly_unparseable_outline_still_yields_nothing(self):
        # Fail-open, but explicitly: no source, no lock, and the eval records
        # narrator_lock_active=False so "unchecked" never reads as "clean".
        self.assertEqual(mc_aliases_for_project("# Empty\n", ""), set())

    def test_a_tie_breaks_to_the_first_listed_name(self):
        # BLOCKER: with a perfectly alternating cast every given name ties, and
        # `max(freq, key=freq.get)` let dict order silently decide who "I" is.
        alternating = """### Chapter 1: A
- **Focus:** Mira Bakersville
### Chapter 2: B
- **Focus:** Lily Bakersville
### Chapter 3: C
- **Focus:** Mira Bakersville
### Chapter 4: D
- **Focus:** Lily Bakersville
"""
        aliases = protagonist_aliases(alternating)
        self.assertIn("mira", aliases, aliases)
        self.assertNotIn("lily", aliases, aliases)


class NarratorLockStateTest(unittest.TestCase):
    """An inactive lock must be distinguishable from a satisfied one."""

    def _log(self, tmp, payload):
        p = Path(tmp) / "eval.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_inactive_is_reported_when_the_mc_is_unknown(self):
        from pipeline.phases.common import (
            narrator_lock_blocks, narrator_lock_was_inactive,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._log(tmp, {"narrator_lock_active": False,
                                   "narrator_violations": []})
            self.assertTrue(narrator_lock_was_inactive(path))
            self.assertFalse(narrator_lock_blocks(path))

    def test_active_and_clean_is_not_inactive(self):
        from pipeline.phases.common import (
            narrator_lock_blocks, narrator_lock_was_inactive,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._log(tmp, {"narrator_lock_active": True,
                                   "narrator_violations": []})
            self.assertFalse(narrator_lock_was_inactive(path))
            self.assertFalse(narrator_lock_blocks(path))

    def test_a_log_without_the_flag_is_treated_as_unchecked(self):
        # Absence is not a pass. An eval log predating the flag must not
        # retroactively certify a chapter as narrator-checked.
        from pipeline.phases.common import narrator_lock_was_inactive
        with tempfile.TemporaryDirectory() as tmp:
            path = self._log(tmp, {"narrator_violations": []})
            self.assertTrue(narrator_lock_was_inactive(path))


if __name__ == "__main__":
    unittest.main()
