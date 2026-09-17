"""The ledger payload must describe the data honestly.

The bug this pins: `recalled` (a payoff with no plant) was coerced to
"paid off" in the console, so a finished 24-chapter novel reported 111 paid
threads where the outline file said 10.
"""

import json
import tempfile
import unittest
from pathlib import Path

from webui import fixtures as fx


def _thread(thread, **kw):
    return fx._thread_row(thread, **kw)


class ThreadRowTest(unittest.TestCase):
    def test_matched_needs_both_ends(self):
        row = _thread("a thread", planted=2, harvest=13,
                      planted_all=[2], harvested_all=[13])
        self.assertEqual("matched", row["status"])
        self.assertEqual(11, row["span"])

    def test_payoff_without_a_plant_is_an_orphan_not_a_paid_off(self):
        row = _thread("orphan payoff", planted=None, harvest=2)
        self.assertEqual("harvest-only", row["status"])
        self.assertIsNone(row["span"])

    def test_plant_without_a_payoff(self):
        row = _thread("unpaid setup", planted=1, harvest=None)
        self.assertEqual("plant-only", row["status"])
        self.assertIsNone(row["span"])

    def test_span_uses_earliest_plant_and_latest_payoff(self):
        """min->min (the old shape) understated every multi-node arc."""
        row = _thread("arc", planted=8, harvest=10,
                      planted_all=[8, 14], harvested_all=[10, 18])
        self.assertEqual(10, row["span"])

    def test_same_chapter_pair_has_a_zero_span(self):
        row = _thread("echo", planted=9, harvest=9)
        self.assertEqual("matched", row["status"])
        self.assertEqual(0, row["span"])

    def test_match_method_reflects_identity(self):
        self.assertEqual("slug", _thread("chalk_arrows", planted=1, harvest=9)["matchMethod"])
        self.assertEqual("inferred",
                         _thread("The chalk arrows hum faintly", planted=1, harvest=9)["matchMethod"])


class PayloadTest(unittest.TestCase):
    """End-to-end over a synthetic project directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.p = Path(self._tmp.name)
        (self.p / ".outline_roadmap.md").write_text(
            "## HIGH-LEVEL ROADMAP\n\n"
            "### Chapter 1: [ordinary_world] First thing happens here.\n"
            "A second sentence of the beat.\n\n"
            "### Chapter 2:\n"
            "No tag on this heading at all.\n\n"
            "### Chapter 3: [swap_incident] Third thing.\n\n"
            "## GLOBAL PLOT THREADS LEDGER\n\n"
            "**1. long_arc**\n"
            "- Planted: Chapter 2 (first) / Chapter 4 (second)\n"
            "- Harvested: Chapter 20 (payoff) and Chapter 24 (coda)\n\n"
            "**2. never_resolved**\n"
            "- Planted: Chapter 7 (setup)\n",
            encoding="utf-8",
        )
        (self.p / "outline.md").write_text(
            "## FORESHADOWING LEDGER\n\n"
            "### Ch 1: A\n**Plants:**\n- A setup about the brass key\n\n"
            "### Ch 5: B\n**Harvests:**\n- The brass key turns in the lock\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _ledger(self, state=None):
        return fx.gen_ledger(self.p, state or {"chapters_total": 24})

    def test_roadmap_keeps_every_chapter_and_numbering(self):
        rm = self._ledger()["roadmap"]
        self.assertEqual([1, 2, 3], [c["chapter"] for c in rm])
        self.assertEqual("ordinary world", rm[0]["title"])   # beat tag, not a body sentence
        self.assertEqual("chapter 2", rm[1]["title"])         # slugless heading
        self.assertTrue(all(c["beats"] for c in rm))

    def test_planned_threads_parse_the_roadmap_heading_shape(self):
        """The roadmap writes '**1. slug**'; parsing only '###' hid every arc."""
        planned = self._ledger()["plannedThreads"]
        self.assertEqual(["long arc", "never resolved"], [t["thread"] for t in planned])
        arc = planned[0]
        self.assertEqual(2, arc["planted"])     # earliest Planted chapter
        self.assertEqual(24, arc["harvest"])    # latest Harvested chapter
        self.assertEqual(22, arc["span"])
        self.assertEqual("slug", arc["matchMethod"])
        self.assertEqual("plant-only", planned[1]["status"])

    def test_no_row_is_called_matched_without_both_ends(self):
        for key in ("threads", "plannedThreads"):
            for t in self._ledger()[key]:
                if t["status"] == "matched":
                    self.assertIsNotNone(t["planted"], f"{key}: {t['thread']}")
                    self.assertIsNotNone(t["harvest"], f"{key}: {t['thread']}")
                    self.assertIsNotNone(t["span"])

    def test_settled_follows_the_finish_state(self):
        self.assertFalse(self._ledger({"chapters_total": 24})["settled"])
        self.assertTrue(self._ledger(
            {"chapters_total": 24, "current_focus": "done"})["settled"])
        self.assertTrue(self._ledger(
            {"chapters_total": 24, "phase": "complete"})["settled"])

    def test_payload_is_json_serializable(self):
        json.dumps(self._ledger())


if __name__ == "__main__":
    unittest.main()
