"""A declared payoff->plant link must beat inference, and must be honest.

The measured problem: pairing v4's payoffs to setups by token overlap left 101
of 116 harvests with no plant, because the two sides are independently written
paraphrases. When a payoff can *name* the chapter that set it up, no similarity
test is needed.
"""

import unittest

from core.micro_plants import match_plant_harvest_threads
from webui import fixtures as fx


class DeclaredLinkTest(unittest.TestCase):
    def test_declared_link_survives_unrelated_wording(self):
        """No shared vocabulary at all — the declaration is the only link."""
        threads = match_plant_harvest_threads(
            [{"text": "a yellow wax seal on the decree scroll", "chapter": 12}],
            [{"text": "recognition dawns, and the kneeling begins", "chapter": 20,
              "declared_chapter": 12}],
        )
        self.assertEqual(1, len(threads))
        t = threads[0]
        self.assertEqual(12, t["planted"])
        self.assertEqual(20, t["harvest"])
        self.assertEqual("declared", t["match"])
        self.assertEqual("paid off", t["status"])

    def test_a_declared_payoff_is_not_left_an_orphan(self):
        """Without the declaration this is exactly the row that showed as 'recalled'."""
        rows = [{"text": "the chalk nub wedged between her fingers", "chapter": 19}]
        harvest = {"text": "the rite is completed with blood and iron", "chapter": 22}

        inferred = match_plant_harvest_threads(rows, [dict(harvest)])
        # Unlinked, so the two sides are separate rows: a plant-only and a
        # payoff with no setup — the shape the console used to call "paid off".
        orphans = [t for t in inferred if t["status"] == "recalled"]
        self.assertEqual(1, len(orphans))
        self.assertEqual("inferred", orphans[0]["match"])

        declared = match_plant_harvest_threads(
            rows, [dict(harvest, declared_chapter=19)])
        self.assertEqual(1, len(declared))
        self.assertEqual("paid off", declared[0]["status"])
        self.assertEqual("declared", declared[0]["match"])

    def test_a_declaration_to_a_later_chapter_does_not_link(self):
        """A payoff cannot resolve a plant that has not happened yet."""
        threads = match_plant_harvest_threads(
            [{"text": "the chalk nub", "chapter": 19}],
            [{"text": "an unrelated closing beat", "chapter": 5,
              "declared_chapter": 19}],
        )
        self.assertNotIn("paid off", [t["status"] for t in threads])
        self.assertNotIn("declared", [t["match"] for t in threads])

    def test_undeclared_harvests_still_fall_back_to_inference(self):
        threads = match_plant_harvest_threads(
            [{"text": "a brass key under the floorboard", "chapter": 2}],
            [{"text": "the brass key turns in the lock", "chapter": 9}],
        )
        self.assertEqual("paid off", threads[0]["status"])
        self.assertEqual("inferred", threads[0]["match"])

    def test_plain_strings_still_work(self):
        """Legacy callers pass strings, not dicts."""
        threads = match_plant_harvest_threads(
            [{"text": "a brass key under the floorboard", "chapter": 2}],
            [{"text": "the brass key turns in the lock", "chapter": 9}],
        )
        self.assertTrue(threads)


class BulletRoundTripTest(unittest.TestCase):
    """The console reads the declared source straight off the outline bullets."""

    def test_payoff_prefix_is_parsed_as_identity(self):
        outline = (
            "### Ch 12: A\n**Plants:**\n- a yellow wax seal on the decree scroll\n\n"
            "### Ch 20: B\n**Harvests:**\n"
            "- [payoff of ch12] recognition dawns, and the kneeling begins\n"
        )
        threads = fx._cluster_threads_from_outline_bullets(outline)
        self.assertEqual(1, len(threads))
        self.assertEqual("declared", threads[0]["match"])
        self.assertEqual(12, threads[0]["planted"])
        self.assertEqual(20, threads[0]["harvest"])
        # The prefix is plumbing, not part of the label.
        self.assertNotIn("[payoff of", threads[0]["thread"])

    def test_unprefixed_bullets_still_cluster(self):
        outline = (
            "### Ch 2: A\n**Plants:**\n- a brass key under the floorboard\n\n"
            "### Ch 9: B\n**Harvests:**\n- the brass key turns in the lock\n"
        )
        threads = fx._cluster_threads_from_outline_bullets(outline)
        self.assertEqual("inferred", threads[0]["match"])
        self.assertEqual("paid off", threads[0]["status"])

    def test_declared_method_reaches_the_console_row(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "outline.md").write_text(
                "### Ch 12: A\n**Plants:**\n- a yellow wax seal on the decree scroll\n\n"
                "### Ch 20: B\n**Harvests:**\n"
                "- [payoff of ch12] recognition dawns, and the kneeling begins\n",
                encoding="utf-8",
            )
            led = fx.gen_ledger(p, {"chapters_total": 24})
        row = led["threads"][0]
        self.assertEqual("matched", row["status"])
        self.assertEqual("declared", row["matchMethod"])
        self.assertEqual(8, row["span"])


if __name__ == "__main__":
    unittest.main()
