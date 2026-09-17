"""The attribution pass, verified without an LLM.

The paid re-measurement (re-summarize v4's chapters and compare span
distributions) needs a live provider; these tests pin the logic deterministically
so the mechanism is guarded regardless.
"""

import json
import unittest
from unittest import mock

from pipeline import build_outline as bo


def _stub(response):
    def _call(prompt, max_tokens=1500):
        _call.prompt = prompt
        return response if isinstance(response, str) else json.dumps(response)
    _call.prompt = None
    return _call


PRIOR = [
    {"chapter": 12, "text": "the yellow blessed wax sealing the decree scroll"},
    {"chapter": 15, "text": "chalk arrows hum faintly in the under-crust"},
]


class AttributeHarvestsTest(unittest.TestCase):
    def test_a_named_plant_becomes_an_attribution(self):
        with mock.patch.object(bo, "call_model", _stub(
                {"attributions": [{"index": 1, "planted_chapter": 12}]})):
            out = bo.attribute_harvests(20, ["the wax finally burns through"], PRIOR)
        self.assertEqual([12], out)

    def test_the_prompt_shows_earlier_plants_and_the_payoffs(self):
        stub = _stub({"attributions": [{"index": 1, "planted_chapter": None}]})
        with mock.patch.object(bo, "call_model", stub):
            bo.attribute_harvests(20, ["the wax finally burns through"], PRIOR)
        self.assertIn("the wax finally burns through", stub.prompt)
        self.assertIn("ch12", stub.prompt)
        self.assertIn("decree scroll", stub.prompt)

    def test_out_of_order_and_invented_chapters_are_refused(self):
        """A payoff cannot resolve its own chapter, a later one, or a made-up one."""
        cases = [
            {"attributions": [{"index": 1, "planted_chapter": 20}]},   # own chapter
            {"attributions": [{"index": 1, "planted_chapter": 99}]},   # later/invented
            {"attributions": [{"index": 1, "planted_chapter": 0}]},    # nonsense
        ]
        for response in cases:
            with self.subTest(response=response):
                with mock.patch.object(bo, "call_model", _stub(response)):
                    out = bo.attribute_harvests(20, ["the wax burns"], PRIOR)
                self.assertEqual([None], out)

    def test_unusable_output_degrades_to_no_attribution(self):
        """Fail-soft: attribution is best-effort, it must not raise."""
        for response in ("not json at all", {}, {"attributions": "wrong type"}):
            with self.subTest(response=response):
                with mock.patch.object(bo, "call_model", _stub(response)):
                    out = bo.attribute_harvests(20, ["a", "b"], PRIOR)
                self.assertEqual([None, None], out)

    def test_a_raising_provider_does_not_propagate(self):
        def boom(prompt, max_tokens=1500):
            raise RuntimeError("403 Forbidden")
        with mock.patch.object(bo, "call_model", boom):
            out = bo.attribute_harvests(20, ["a"], PRIOR)
        self.assertEqual([None], out)

    def test_no_prior_plants_means_no_call_at_all(self):
        stub = _stub({"attributions": []})
        with mock.patch.object(bo, "call_model", stub):
            out = bo.attribute_harvests(1, ["an opening payoff"], [])
        self.assertEqual([None], out)
        self.assertIsNone(stub.prompt)  # nothing to ask about

    def test_every_payoff_gets_an_entry(self):
        with mock.patch.object(bo, "call_model", _stub(
                {"attributions": [{"index": 2, "planted_chapter": 15}]})):
            out = bo.attribute_harvests(20, ["first", "second", "third"], PRIOR)
        self.assertEqual([None, 15, None], out)


class AttributeEntriesTest(unittest.TestCase):
    def test_entries_are_rewritten_to_the_attributed_shape(self):
        entries = [
            {"num": 12, "plants": ["the yellow wax"], "harvests": []},
            {"num": 20, "plants": [], "harvests": ["the wax burns through"]},
        ]
        with mock.patch.object(bo, "call_model", _stub(
                {"attributions": [{"index": 1, "planted_chapter": 12}]})):
            bo.attribute_entries(entries)
        self.assertEqual("the wax burns through", entries[1]["harvests"][0]["thread"])
        self.assertEqual(12, entries[1]["harvests"][0]["declared_chapter"])

    def test_short_answers_are_padded_not_truncated(self):
        """Losing a payoff would silently shrink the ledger."""
        entries = [{"num": 12, "plants": ["setup"]},
                   {"num": 20, "harvests": ["one", "two", "three"]}]
        with mock.patch.object(bo, "call_model", _stub(
                {"attributions": [{"index": 1, "planted_chapter": 12}]})):
            bo.attribute_entries(entries)
        self.assertEqual(3, len(entries[1]["harvests"]))
        self.assertEqual(12, entries[1]["harvests"][0]["declared_chapter"])
        self.assertIsNone(entries[1]["harvests"][2]["declared_chapter"])

    def test_an_entry_with_no_harvests_is_untouched(self):
        entries = [{"num": 3, "plants": ["a"], "harvests": []}]
        with mock.patch.object(bo, "call_model", _stub({"attributions": []})):
            bo.attribute_entries(entries)
        self.assertEqual([], entries[0]["harvests"])


class HarvestShapeTest(unittest.TestCase):
    def test_both_shapes_read_back(self):
        self.assertEqual(("text", None), bo._harvest_text_and_source("text"))
        self.assertEqual(("text", 7),
                         bo._harvest_text_and_source(
                             {"thread": "text", "declared_chapter": 7}))


if __name__ == "__main__":
    unittest.main()
